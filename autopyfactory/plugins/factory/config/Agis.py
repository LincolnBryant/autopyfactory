#!/usr/bin/env python
from __future__ import print_function

import logging
# Set up logging. 
# Add TRACE level
logging.TRACE = 5
logging.addLevelName(logging.TRACE, 'TRACE')
    
# Create trace log function and assign
def trace(self, msg, *args, **kwargs):
    self.log(logging.TRACE, msg, *args, **kwargs)
logging.Logger.trace = trace

import json
import os
import sys
import traceback
from urllib import urlopen


# Added to support running module as script from arbitrary location. 
from os.path import dirname, realpath, sep, pardir
fullpathlist = realpath(__file__).split(sep)
print(fullpathlist)
prepath = sep.join(fullpathlist[:-5])
sys.path.insert(0, prepath)

from autopyfactory.apfexceptions import ConfigFailure
from autopyfactory.configloader import Config, ConfigManager
from autopyfactory.interfaces import ConfigInterface

#####################################################
#
#  Edittable constants/maps. Will be moved to config file(s)
#
#####################################################

# Used to calculate scale factory, along with CEs per PQ  
JOBS_PER_PILOT = 1.5
NUM_FACTORIES = 4.0
APF_DEFAULT = '''
[DEFAULT]
vo = ATLAS
status = online
override = True
enabled = True
cleanlogs.keepdays = 21
# plugins
batchstatusplugin = Condor
wmsstatusplugin = Panda
schedplugin = Ready
monitorsection = apfmon-lancaster
schedplugin = Ready, Scale, MaxPerCycle, MinPerCycle, StatusTest, StatusOffline, MaxPending
sched.maxtorun.maximum = 9999
sched.maxpending.maximum = 100
sched.maxpercycle.maximum = 50
sched.minpercycle.minimum = 0
sched.statusoffline.allowed = True
sched.statusoffline.pilots = 0
sched.statustest.allowed = True
sched.statustest.pilots = 1
executable = /usr/libexec/wrapper.sh
executable.defaultarguments = -s %(wmsqueue)s -h %(batchqueue)s -p 25443 -w https://pandaserver.cern.ch
req = requirements = JobRunCount == 0
hold = periodic_hold = ( JobStatus==1 && gridjobstatus=?=UNDEFINED && CurrentTime-EnteredCurrentStatus>3600 ) || ( JobStatus == 1 && (JobRunCount =!= UNDEFINED && JobRunCount > 0) ) || ( JobStatus == 2 && CurrentTime-EnteredCurrentStatus>604800 )
remove = periodic_remove = (JobStatus == 5 && (CurrentTime - EnteredCurrentStatus) > 3600) || (JobStatus == 1 && globusstatus =!= 1 && (CurrentTime - EnteredCurrentStatus) > 86400)
apfqueue.sleep = 120

'''
# REQ maps list *required* attribute and values. Object is removed if absent. 
# NEG maps list *prohibited* attribute and values. Object is removed if present. 
PQFILTERREQMAP = { 'pilot_manager' : ['apf'],
                   'resource_type' : ['grid'],
                   'vo_name'       : ['atlas'],
                   'site_state' : ['active']
                   } 

PQFILTERNEGMAP = { } 

CQFILTERREQMAP = {'ce_state' : ['active'],
                   'ce_status' : ['production'],
                   'ce_queue_status'   : ['production',''],
               }
CQFILTERNEGMAP = { 'ce_flavour' : ['lcg-ce'], }

######################################################
#
#   DO NOT EDIT BELOW THIS LINE
#
#######################################################

class AgisPandaQueue(object):
    
    def __init__(self, d, key):
        self.log = logging.getLogger("main.agis")
        self.panda_queue_name = key
        self.panda_resource = d[key]['panda_resource']              # AGLT2_LMEM     
        
        # Will be calculated later... 
        self.numvalidces = 0.0
        
        self.corecount = d[key]['corecount']
        self.memory = d[key]['memory']
        self.maxtime = d[key]['maxtime']
        self.maxmemory = d[key]['maxmemory']
        self.site_state = d[key]['site_state'].lower()
        self.maxrss = d[key].get('maxrss', 0)
        self.maxswap = d[key].get('maxswap', 0)
        self.pilot_manager = d[key]['pilot_manager'].lower()
        self.pilot_version = d[key].get('pilot_version', 'current')
        self.resource_type = d[key]['resource_type'].lower()        # grid
        self.type = d[key]['type'].lower()                          # production 
        self.vo_name = d[key]['vo_name'].lower()                    # atlas
        self.queues = d[key]['queues']                              # list of ditionaries
        #self.ce_queues = self._make_cequeues(d, key)
        self.ce_queues = self._make_cequeues(self.queues)

    def __str__(self):
        s = "AgisPandaQueue: "
        s += "panda_resource=%s " %  self.panda_resource
        for ceq in self.ce_queues:
            s += "   %s" % ceq
        return s

    def _make_cequeues(self, celist):
        '''
          Makes CEqueue objects, key is PQ name, cek is ce queue dictionary
        '''
        self.log.trace("Handling cequeues for PQ %s" % self.panda_queue_name)
        cequeues = []
        for cedict in celist:
            self.log.trace("Handling cedict %s" % cedict)
            try:
                #cqo = AgisCEQueue(jdoc, key, cek)
                cqo = AgisCEQueue( self, cedict)
                cequeues.append( cqo)
            except Exception, e:
                self.log.error('Failed to create AgisCEQueue for PQ %s and CE %s' % (self.panda_queue_name, cedict))
                self.log.error("Exception: %s" % traceback.format_exc())
        self.log.trace("Made list of %d CEQ objects" % len(cequeues))
        return cequeues    
    
class AgisCEQueue(object):
    '''
    Represents a single CE queue within a Panda queue description.  
    ['queues']
    '''
    def __init__(self, parent, cedict ):
        self.log = logging.getLogger("main.agis")
        self.parent = parent
        self.panda_queue_name = parent.panda_queue_name 
        self.ce_name = cedict['ce_name']                  # AGLT2-CE-gate04.aglt2.org
        self.ce_endpoint = cedict['ce_endpoint']          # gate04.aglt2.org:2119
        self.ce_host = self.ce_endpoint.split(":")[0]
        self.ce_state = cedict['ce_state'].lower()
        self.ce_status = cedict['ce_status'].lower()
        self.ce_queue_status = cedict['ce_queue_status'].lower()
        self.ce_flavour = cedict['ce_flavour'].lower()        # GLOBUS
        self.ce_version = cedict['ce_version'].lower()        # GT5
        self.ce_queue_name = cedict['ce_queue_name']          # default
        self.ce_jobmanager = cedict['ce_jobmanager'].lower()  # condor
        self.apf_scale_factor = 1.0

        # Empty/default attributes:
        self.gridresource = None
        self.submitplugin = None
        self.submitpluginstr = None
        self.gramversion = None
        self.gramqueue = None
        self.creamenv = None
        self.creamattr = ''
        self.condorattr = False

        if self.ce_flavour in ['osg-ce','globus']:
            self.gridresource = '%s/jobmanager-%s' % (self.ce_endpoint, self.ce_jobmanager)
            if self.ce_version == 'gt2':
                self.submitplugin = 'CondorGT2'
                self.submitpluginstr = 'condorgt2'
            elif self.ce_version == 'gt5':
                self.submitplugin = 'CondorGT5'
                self.submitpluginstr = 'condorgt5'                
                self.gramversion = 'gram5'
                self.gramqueue = self.ce_queue_name
        
        elif self.ce_flavour == 'cream-ce':
            self.gridresource = '%s/ce-cream/services/CREAM2 %s %s' % (self.ce_endpoint, 
                                                                       self.ce_jobmanager, 
                                                                       self.ce_queue_name)
            self.submitplugin = 'CondorCREAM'
            self.submitpluginstr = 'condorcream'
            # glue 1.3 uses minutes and this / operator uses floor value
            # https://wiki.italiangrid.it/twiki/bin/view/CREAM/UserGuideEMI2#Forward_of_requirements_to_the_b
            self.maxtime = self.maxtime / 60
            self.creamattr += ' CpuNumber=%d;WholeNodes=false;SMPGranularity=%d; ' % (self.parent.corecount,
                                                                                      self.parent.corecount) 

        elif self.ce_flavour == 'arc-ce':
            pass
        
        elif self.ce_flavour == 'htcondor-ce':
            self.gridresource = self.ce_host
            self.submitplugin = 'CondorOSGCE'
            self.submitpluginstr = 'condorosgce'
            
        else:
            self.log.warning("Unknown ce_flavour: %s" % self.ce_flavour)
        
        
    def getApfConf(self):
        '''
        Returns string of valid APF configuration for this queue-ce entry.  
        '''
        
        # Unconditional config
        s = "[%s-%s] \n" % ( self.panda_queue_name, self.ce_host )
        s += "enabled=True\n"
        s += "batchqueue = %s \n" % self.panda_queue_name        
        s += "wmsqueue = %s \n" % self.parent.panda_resource
        
        
        try:       
            self.apf_scale_factor = ((( 1.0 / NUM_FACTORIES) / self.parent.numvalidces ) / JOBS_PER_PILOT) 
        except ZeroDivisionError:
            self.log.error("Division by zero. Something wrong with scale factory calc.")
            self.apf_scale_factor = 1.0
        s += "sched.scale.factor = %f \n" % self.apf_scale_factor
        
        s += "batchsubmitplugin = %s \n" % self.submitplugin
        s += "batchsubmit.%s.gridresource = %s \n" % (self.submitpluginstr, self.gridresource)
        

        if self.creamenv is not None:
            s += 'batchsubmit.condorcream.environ = %s' % self.creamenv
            if self.creamattr is not None:
                s += 'creamattr = %s' % self.creamattr
                s += 'batchsubmit.condorcream.condor_attributes = %(req)s,%(hold)s,%(remove)s,cream_attributes = %(creamattr)s,notification=Never'
            else:
                s += 'batchsubmit.condorcream.condor_attributes = %(req)s,%(hold)s,%(remove)s,notification=Never'
        

        
        return s 

    def __str__(self):
        s = "AgisCEQueue: "
        s += "PQ=%s " %  self.panda_queue_name
        s += "wmsqueue=%s" % self.parent.panda_resource
        s += "submitplugin=%s" % self.submitplugin
        s += "host=%s" % self.ce_host
        s += "endpoint=%s" %self.ce_endpoint
        s += "gridresource=%s" % self.gridresource
        s += "maxtime=%s" % self.parent.maxtime
        return s

class Agis(ConfigInterface):
    """
    creates the configuration files with 
    information retrieved from AGIS
    """

    
    def __init__(self, factory):
        self.log = logging.getLogger("main.agis")
        if factory is not None:
            
            self.factory = factory
            self.fcl = factory.fcl
        else:
            self.baseurl = 'http://atlas-agis-api.cern.ch/request/pandaqueue/query/list/?json&preset=schedconf.all'
            self.vos = ['atlas',]
            self.clouds = ['US',]
            self.activities = ['analysis','production']
            self.queuesdefault = "~/etc/autopyfactory/agisdefaults.conf"
            self.basescale = .20
            self.sleep = 120
        self.log.trace('ConfigPlugin: Object initialized.')

    def getConfig(self):
        '''
        For embedded usage. Handles everything in config.  
        '''
        allqueues = []
        for v in self.vos:
            for c in self.clouds:
                for a in self.activities:
                    self.log.debug("Handling vo=%s cloud=%s activity=%s" % (v,c,a))
                    d = self._downloadJSON(vo, cloud)
                    self.log.trace("Calling _handleJSON")
                    queues = self._handleJSON(d, a)
                    self.log.debug("AGIS provided %d queues for activity %s" % ( len(queues), a))
                    for q in queues:
                        allqueues.append(q)
        self.log.debug("AGIS provided list of %d total queues." % len(allqueues))
        goodqueues = self._filterobjs(allqueues, PQFILTERREQMAP, PQFILTERNEGMAP)
        cequeues = []
        for q in goodqueues:
            for cq in q.ce_queues:
                cequeues.append(cq)
        self.log.debug("Assembled list of %d CEs from AGIS." % len(cequeues))
        goodcequeues = self._filterobjs(cequeues, CQFILTERREQMAP, CQFILTERNEGMAP )
        
        self._setcenum(goodcequeues)
                
        s = ""
        s += APF_DEFAULT
        for c in goodcequeues:
            s += "%s\n" % c.getApfConf()        
        return s
            
    def _setcenum(self, celist):
        '''
        Calculates the number of still-valid CEs serving a Panda queue, and 
        sets the <numces> attribute on parent PQ. 
        '''
        for cq in celist:
            cq.parent.numvalidces += 1.0

  
    def _filterobjs(self, objlist, reqdict=None, negdict=None):
        '''
        Generic filtering method. 
        '''
        goodobjs = []
        kept = 0
        filtered = 0
        for ob in objlist:
            keep = True
            for attrstr in reqdict.keys():
                self.log.trace("Checking obj %s for value %s" % (attrstr, reqdict[attrstr]))
                if getattr(ob, attrstr) not in reqdict[attrstr]:
                    keep = False
            if keep:
                goodobjs.append(ob)
                kept += 1
            else:
                self.log.trace("Remove obj %s" % ob)
                filtered += 1
        self.log.trace("Keeping %d CQs, filtered %d CQs" % (kept, filtered))
        return goodobjs    

    
    def _downloadJSON(self, vo, cloud):
        url = '%s&vo_name=%s&cloud=%s' % (self.baseurl, vo, cloud)
        self.log.trace('Contacting %s' % url)
        handle = urlopen(url)
        d = json.load(handle, 'utf-8')
        handle.close()
        self.log.trace('Done.')
        return d

    def _handleJSON(self, jsondoc, activity):
        '''
        Returns all PQ objects in list.  
        
        '''
        self.log.trace("handleJSON called for activity %s" % activity)
        queues = []
        for key in sorted(jsondoc):
            self.log.trace("key = %s" % key)
            try:
                qo = AgisPandaQueue(jsondoc, key)
                queues.append(qo)
            except Exception, e:
                self.log.error('Failed to create AgisPandaQueue %s Exception: %s' % (key,
                                                                                     traceback.format_exc()
                                                                                     ) )
        self.log.trace("Made list of %d PQ objects" % len(queues))
        return queues
    

# -------------------------------------------------------------------
#   For stand-alone usage
# -------------------------------------------------------------------
if __name__ == '__main__':
    import logging
    import getopt
    import sys
    import os
    from ConfigParser import ConfigParser, SafeConfigParser
    
    debug = 0
    info = 0
    trace = 0
    vo = 'atlas'
    cloud = 'US'
    activity = 'analysis'
    outfile = '/tmp/agis-apf-config.conf'
    fconfig_file = None
    default_configfile = os.path.expanduser("~/etc/autopyfactory.conf")
         
    usage = """Usage: Agis.py [OPTIONS]  
    OPTIONS: 
        -h --help                   Print this message
        -d --debug                  Debug messages
        -v --verbose                Verbose information
        -t --trace                  Trace level info
        -c --config                 Config file [~/etc/autopyfactory.conf]
        -C --cloud                  Cloud ['US']
        -V --vo                     Virtual organization ['atlas']
        -A --activity               Activity ['analysis']
        -D --default                Defaults file [~/etc/defaults.conf
        
        """
    
    # Handle command line options
    argv = sys.argv[1:]
    try:
        opts, args = getopt.getopt(argv, 
                                   "c:hdvtCVAo:D:", 
                                   ["config=",
                                    "help", 
                                    "debug", 
                                    "verbose",
                                    "trace",
                                    "cloud=",
                                    "vo=",
                                    "activity=",
                                    "outfile=",
                                    "defaults="
                                    ])
    except getopt.GetoptError, error:
        print( str(error))
        print( usage )                          
        sys.exit(1)
    for opt, arg in opts:
        if opt in ("-h", "--help"):
            print(usage)                     
            sys.exit()            
        elif opt in ("-c", "--config"):
            fconfig_file = arg
        elif opt in ("-d", "--debug"):
            debug = 1
        elif opt in ("-v", "--verbose"):
            info = 1
        elif opt in ("-t", "--trace"):
            trace = 1
        elif opt in ("-C", "--cloud"):
            cloud = arg 
        elif opt in ("-V", "--vo"):
            vo = arg
        elif opt in ("-A", "--activity"):
            activity = arg            
        elif opt in ("-o", "--outfile"):
            outfile = arg
        elif opt in ("-D", "--defaults"):
            defaultsfile = arg
 
            
            
    # Set up logging. 
    # Add TRACE level
    #logging.TRACE = 5
    #logging.addLevelName(logging.TRACE, 'TRACE')
    
    # Create trace log function and assign
    #def trace(self, msg, *args, **kwargs):
    #    self.log(logging.TRACE, msg, *args, **kwargs)
    #logging.Logger.trace = trace
    
    # Check python version 
    major, minor, release, st, num = sys.version_info
    
    # Set up logging, handle differences between Python versions... 
    # In Python 2.3, logging.basicConfig takes no args
    #
    FORMAT23="[ %(levelname)s ] %(asctime)s %(filename)s (Line %(lineno)d): %(message)s"
    FORMAT24=FORMAT23
    FORMAT25="[%(levelname)s] %(asctime)s %(module)s.%(funcName)s(): %(message)s"
    FORMAT26=FORMAT25
    
    if major == 2:
        if minor ==3:
            formatstr = FORMAT23
        elif minor == 4:
            formatstr = FORMAT24
        elif minor == 5:
            formatstr = FORMAT25
        elif minor == 6:
            formatstr = FORMAT26
        elif minor == 7:
            formatstr = FORMAT26
    
    log = logging.getLogger()
    hdlr = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(formatstr)
    hdlr.setFormatter(formatter)
    log.addHandler(hdlr)
    
    log.setLevel(logging.WARNING)
    if debug: 
        log.setLevel(logging.DEBUG) # Override with command line switches
    if info:
        log.setLevel(logging.INFO) # Override with command line switches
    if trace:
        log.setLevel(logging.TRACE) 
    log.debug("Logging initialized.")      
    
    # Read in config file
    #fconfig=ConfigParser()
    #if not fconfig_file:
    #    fconfig_file = os.path.expanduser(default_configfile)
    #else:
    #    fconfig_file = os.path.expanduser(aconfig_file)
    #got_config = fconfig.read(aconfig_file)
    #log.trace("Read config file %s, return value: %s" % (fconfig_file, got_config))

    
    log.debug("Creating Agis object")  
    acp = Agis(None)
    log.debug("Agis object created")
    configstr = acp.getConfig()
    log.debug("Got config string for writing to outfile %s" % outfile)
    # outfile = "./%s" % outfile
    outfile = os.path.expanduser(outfile)
    f = open(outfile, 'w')
    f.write(configstr)
    f.close()
    
    
    
    
