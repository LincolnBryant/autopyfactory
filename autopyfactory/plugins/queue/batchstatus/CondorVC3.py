#!/bin/env python
#
# AutoPyfactory batch status plugin for Condor
#
import commands
import subprocess
import logging
import os
import sys
import time
import threading
import traceback

from datetime import datetime
from pprint import pprint
from autopyfactory.interfaces import BatchStatusInterface, _thread
from autopyfactory.info import BatchStatusInfo
from autopyfactory.info import QueueInfo
from autopyfactory.condorlib import querycondorlib, condor_q
from autopyfactory.mappings import map2info
import autopyfactory.utils as utils



from autopyfactory.condorlib import HTCondorPool
from autopyfactory import info2



class CondorJobInfo(object):
    """
    This object represents a Condor job.     
        
    """
    jobattrs = ['match_apf_queue',
                'clusterid',
                'procid',
                'qdate', 
                'enteredcurrentstatus',
                'jobstatus',
                'gridjobstatus',
                 ]  


    def __init__(self, dict):
        """
        Creates CondorJobInfo object from arbitrary dictionary of attributes. 
        
        """
        self.log = logging.getLogger('autopyfactory.batchstatus')
        self.jobattrs = []
        for k in dict.keys():
            self.__setattr__(k,dict[k])
            self.jobattrs.append(k)
        self.jobattrs.sort()
        #self.log.debug("Made CondorJobInfo object with %d attributes" % len(self.jobattrs))    
        
    def __str__(self):
        s = "CondorJobInfo: %s.%s " % (self.clusterid, 
                                      self.procid)
        #for k in CondorJobInfo.jobattrs:
        #    s += " %s=%s " % ( k, self.__getattribute__(k))
        for k, v in self.__dict__.items():
            s += " %s=%s " % ( k, v)
    
    def __repr__(self):
        s = str(self)
        return s


class _condor(_thread, BatchStatusInterface):
    """
    -----------------------------------------------------------------------
    This class is expected to have separate instances for each object. 
    The first time it is instantiated, 
    -----------------------------------------------------------------------
    Public Interface:
            the interfaces inherited from Thread and from BatchStatusInterface
    -----------------------------------------------------------------------
    """
    def __init__(self, apfqueue, config, section):
        _thread.__init__(self)
        apfqueue.factory.threadsregistry.add("plugin", self)      
        self.log = logging.getLogger('autopyfactory.batchstatus.%s' %apfqueue.apfqname)
        self.log.debug('BatchStatusPlugin: Initializing object...')

        self.apfqueue = apfqueue
        self.apfqname = apfqueue.apfqname
        
        try:
            self.condoruser = apfqueue.fcl.get('Factory', 'factoryUser')
            self.factoryid = apfqueue.fcl.get('Factory', 'factoryId')
            self.maxage = apfqueue.fcl.generic_get('Factory', 'batchstatus.condor.maxage', default_value=360) 
            self.sleeptime = self.apfqueue.fcl.getint('Factory', 'batchstatus.condor.sleep')
            self.scheddhost = self.apfqueue.qcl.generic_get(self.apfqname, 'batchstatus.condor.scheddhost', default_value=None) 
            self.scheddport = self.apfqueue.qcl.generic_get(self.apfqname, 'batchstatus.condor.scheddport')
            self.queryargs = self.apfqueue.qcl.generic_get(self.apfqname, 'batchstatus.condor.queryargs') 
            
            # if an Overlay system is present, get the collector host and port
            self.overlay = self.apfqueue.qcl.generic_get(self.apfqname, 'batchstatus.condor.overlay', default_value=None) 
            self.collectorhost = self.apfqueue.qcl.generic_get(self.apfqname, 'batchstatus.condor.overlay.collectorhost', default_value=None)
            self.collectorport = self.apfqueue.qcl.generic_get(self.apfqname, 'batchstatus.condor.overlay.collectorport', default_value=9618)

            self.htcondor = HTCondorPool(self.collectorhost, self.scheddhost)

            # deprecated
            if self.queryargs:
                l = self.queryargs.split()  # convert the string into a list
                if '-name' in l:
                    self.remoteschedd = l[l.index('-name') + 1]
                if '-pool' in l:
                    self.remotecollector = l[l.index('-pool') + 1]
                    
        except AttributeError:
            self.condoruser = 'apf'
            self.factoryid = 'test-local'
            self.sleeptime = 10
            self.log.warning("Got AttributeError during init. We should be running stand-alone for testing.")

        self._thread_loop_interval = self.sleeptime
        self.currentinfo = None
        self.jobinfo = None              
        self.last_timestamp = 0

        # mappings
        self.jobstatus2info = self.apfqueue.factory.mappingscl.section2dict('CONDORBATCHSTATUS-JOBSTATUS2INFO')
        self.log.info('jobstatus2info mappings are %s' %self.jobstatus2info)

        # query attributes
        self.condor_q_attribute_l = ['match_apf_queue', 
                                     'jobstatus',
                                     'factory_jobid'
                                    ]
        self.condor_history_attribute_l = ['match_apf_queue', 
                                          'jobstatus', 
                                          'enteredcurrentstatus', 
                                          'remotewallclocktime',
                                          'qdate',
                                          'factory_jobid'
                                          ]
        self.condor_status_attribute_l =  ['factory_jobid',
                                          'activity',
                                          'partitionableslot',
                                          'childactivity'
                                          ]


        # variable to record when was last time info was updated
        # the info is recorded as seconds since epoch
        self.lasttime = 0
        self.log.info('BatchStatusPlugin: Object initialized.')


    def _run(self):
        """
        Main loop
        """
        self.log.debug('Starting')
        self._updatelib()
        self.log.debug('Leaving')


    def getJobInfo(self, queue=None):
        """
        Returns a  object populated by the analysis 
        over the output of a condor_q command

        If the info recorded is older than that maxage,
        None is returned, as we understand that info is too old and 
        not reliable anymore.
        """           
        self.log.debug('Starting with self.maxage=%s' % self.maxage)
        
        if self.jobinfo is None:
            self.log.debug('Not initialized yet. Returning None.')
            return None
        else:
            if queue:
                self.log.debug('Current info is %s' % self.jobinfo)                    
                self.log.debug('Leaving and returning info of %d entries.' % len(self.jobinfo))
                return self.jobinfo[queue]
            else:
                self.log.debug('Current info is %s' % self.jobinfo)
                self.log.debug('No queue given, returning entire BatchStatusInfo object')
                return self.jobinfo

    
    def _updatelib(self):
        self.Lock.acquire()
        self._updatenewinfo()
        self.last_timestamp = time.time()
        self.Lock.release()

    def getInfo(self, algorithm=None):
        """
        Returns a  object populated by the analysis 
        over the output of a condor_q command

        If the info recorded is older than that maxage,
        None is returned, as we understand that info is too old and 
        not reliable anymore.
        """           
        self.log.debug('Starting with self.maxage=%s' % self.maxage)
        
        if self.currentnewinfo is None:
            self.log.debug('Not initialized yet. Returning None.')
            return None

        if self.maxage > 0 and\
           (int(time.time()) - self.currentnewinfo.timestamp) > self.maxage:
            self.log.debug('Info too old. Leaving and returning None.')
            return None

        if not algorithm:
            self.log.debug('Returning current info data as it is.')
            return self.currentnewinfo
          
        if algorithm not in self.cache.keys():
            # FIXME !!
            # this trick does not really work
            # 2 instances of class Algorithm, 
            # even though they host the same sequence of Analyzers, 
            # they are different objects, and therefore 2 different keys
            self.log.debug('There is not processed data in the cache for algorithm. Calculating it.')
            out = algorithm.analyze(self.currentnewinfo)
            self.cache[algorithm] = out
        self.log.debug('Returning processed data.')
        return self.cache[algorithm]
        

    
    def _updatenewinfo(self):
        """
        Query Condor for job status, and populate  object.
        It uses the condor python bindings.
        """
        self.log.debug('Starting.')
        try:
            #condor_q_attribute_l = ['match_apf_queue', 
            #                        'jobstatus'
            #                       ]
            self.condor_q_classad_l = self.htcondor.condor_q(self.condor_q_attribute_l)
            self.log.debug('output of condor_q: %s' % self.condor_q_classad_l)

            #condor_history_attribute_l = ['match_apf_queue', 
            #                              'jobstatus', 
            #                              'enteredcurrentstatus', 
            #                              'remotewallclocktimeqdate'
            #                             ]
            self.condor_history_classad_l = self.htcondor.condor_history(self.condor_history_attribute_l)
            self.log.debug('output of condor_history: %s' % self.condor_history_classad_l)

            #self.condor_status_attribute_l = ['factory_jobid',
            #                                 'activity',
            #                                 'partitionableslot',
            #                                 'childactivity'
            #                                 ]
            self.condor_status_classad_l = self.htcondor.condor_status(self.condor_status_attribute_l)
            self.log.debug('output of condor_status: %s' % self.condor_history_classad_l)

            jobdata = self.condor_q_classad_l + self.condor_history_classad_l + self.condor_status_classad_l

            jobinfo = []
            for job_ad in jobdata:
                for machine_ad in self.condor_status_classad_l:
                    if job_ad["factory_jobid"] == machine_ad["factory_jobid"]:
                        self.log.debug("job ad %s matches matches machine ad %s" % (job_ad, machine_ad))
                        j = dict(job_ad)
                        m = dict(machine_ad)
                        m.update(j) # merge job j's classads with machine m's classads
                        jobinfo.append(m)

            self.currentnewinfo = info2.StatusInfo(jobinfo)

            self.cache = {}

        except Exception, e:
            self.log.error("Exception: %s" % str(e))
            self.log.debug("Exception: %s" % traceback.format_exc())
        self.log.debug('Leaving.')


        
    def add_query_attributes(self, new_q_attr_l=None, new_history_attr_l=None):
        """
        adds new classads to be included in condor queries
        :param list new_q_attr_l: list of classads for condor_q
        :param list new_history_attr_l: list of classads for condor_history
        """
        self.__add_q_attributes(new_q_attr_l)
        self.__add_history_attributes(new_history_attr_l)
        if new_q_attr_l or new_history_attr_l:
            self._updatelib()


    def __add_q_attributes(self, new_q_attr_l):
        """
        adds new classads to be included in condor_q queries
        :param list new_q_attr_l: list of classads for condor_q
        """
        if new_q_attr_l:
            for attr in new_q_attr_l:
                if attr not in self.condor_q_attribute_l:
                    self.condor_q_attribute_l.append(attr)


    def __add_history_attributes(self, new_history_attr_l):
        """
        adds new classads to be included in condor_history queries
        :param list new_history_attr_l: list of classads for condor_history
        """
        if new_history_attr_l:
            for attr in new_history_attr_l:
                if attr not in self.condor_history_attribute_l:
                    self.condor_history_attribute_l.append(attr)
            




# =============================================================================
#       Singleton wrapper
# =============================================================================
class Condor(object):
   
    instances = {}

    def __new__(cls, *k, **kw): 

        # ---------------------------------------------------------------------
        # get the ID
        apfqueue = k[0]
        conf = k[1]
        section = k[2]
        
        id = 'local'
        if conf.generic_get(section, 'batchstatusplugin') == 'Condor':
            queryargs = conf.generic_get(section, 'batchstatus.condor.queryargs')
            if queryargs:
                l = queryargs.split()  # convert the string into a list
                                       # e.g.  ['-name', 'foo', '-pool', 'bar'....]
                name = ''
                pool = ''
        
                if '-name' in l:
                    name = l[l.index('-name') + 1]
                if '-pool' in l:
                    pool = l[l.index('-pool') + 1]
        
                if name == '' and pool == '':
                    id = 'local'
                else:
                    id = '%s:%s' %(name, pool)
        # ---------------------------------------------------------------------

        if not id in Condor.instances.keys():
            Condor.instances[id] = _condor(*k, **kw)
        return Condor.instances[id]



###############################################################################

def test1():
    from autopyfactory.test import MockAPFQueue
    
    a = MockAPFQueue('BNL_CLOUD-ec2-spot')
    bsp = CondorBatchStatusPlugin(a, condor_q_id='local')
    bsp.start()
    while True:
        try:
            time.sleep(15)
        except KeyboardInterrupt:
            bsp.stopevent.set()
            sys.exit(0)    


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    test1()


