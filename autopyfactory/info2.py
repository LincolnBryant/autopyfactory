#! /usr/bin/env python

__author__ = "Jose Caballero"
__email__ = "jcaballero@bnl.gov"

"""
Code to store and manipulate data.

-------------------------------------------------------------------------------
                            class StatusInfo
-------------------------------------------------------------------------------

The data stored by instances of class StatusInfo must be a list of items. 
These items can be anything, including objects. 
A typical example is data is a list of HTCondor ClassAds, where each 
item in the data list represents an HTCondor job.

Class StatusInfo has several methods to manipulate the data, 
but in all cases the output of the method is a new instance StatusInfo.
Methods never modify the current instance data.
This allows to perform different manipulations from the same source object.

There are two types of methods in class StatusInfo:

    - methods whose returned StatusInfo instance accepts further processing.
      Examples are methods indexby(), filter(), and map().

    - methods whose returned StatusInfo instance can not be processed anymore.
      An attempt to call any method on a StatusInfo instance of this type 
      will raise an ObjectIsNotMutable Exception.
      Examples are methods reduce(), and process().

The method indexby() is somehow special. 
It is being used to split the stored data into a dictionary, 
according to whatever rule is provided. 
The values of this dictionary are themselves new StatusInfo instances. 
Therefore, the output of calling indexby() once is an StatusInfo object 
with data:
        
    self.data = {
                 key1: <StatusInfo>,
                 key2: <StatusInfo>,
                 ...
                 keyN: <StatusInfo>
                }

Given the fact that an StatusInfo instance can therefore contain other
StatusInfo instances, all methods are implemented to navigate the tree
and apply themselves only at the deepest level.
      

-------------------------------------------------------------------------------
                            Analyzers 
-------------------------------------------------------------------------------


The input to all methods is an object of type Analyzer. 
Analyzers are classes that implement the rules or policies to be used 
for each method call.  For example: 
    - a call to method groupby() expect an object of type AnalyzerIndexBy
    - a call to method map() expect an object of type AnalyzerMap
    - a call to method reduce() expect an object of type AnalyzerReduce
    - etc.

Each Analyzer object must implement itself a method with the same name
that the StatusInfo's method it is intended for. For exmple:

    - class AnalyzerIndexBy must implement method indexby()
    - class AnalyzerMap must implement method map()
    - class AnalyzerReduce must implement method reduce()
    - ...

Passing an analyzer object that does not implement the right method will 
raise an IncorrectAnalyzer Exception.

A few basic Analyzers have been implemented, ready to use. 
"""

import datetime
import inspect
import logging
import logging.handlers
import threading
import time
import traceback
import os
import pwd
import sys

# =============================================================================
#  Decorators 
#
#   Note:
#   the decorator must be implemented before the class StatusInfo,
#   otherwise, it does not find it
# =============================================================================

def validate_call(method):
    def wrapper(self, analyzer, *k, **kw):
        method_name = method.__name__
        analyzertype = analyzer.analyzertype
        if not self.is_mutable:
            msg = 'Attempting to manipulate data for an object that is not mutable.'
            msg += 'Raising exception.'
            self.log.error(msg)
            raise ObjectIsNotMutable(name)
        if not analyzertype == method_name:
            msg = 'Analyzer object {obj} is not type {name}. Raising exception.'
            msg = msg.format(obj = analyzer,
                             name = method_name)
            self.log.error(msg)
            raise IncorrectAnalyzer(analyzer, analyzertype, method_name)
        out = method(self, analyzer, *k, **kw)
        return out
    return wrapper


# =============================================================================
# Info class
# =============================================================================

class StatusInfo(object):
    # FIXME !! description is needed here !!!
    """
    """

    def __init__(self, data, is_raw=True, is_mutable=True, timestamp=None):
        """ 
        :param data: the data to be recorded
        :param is_raw boolean: indicates if the object is primary or it is composed by other StatusInfo objects
        :param is_mutable boolean: indicates if the data can still be processed or not
        :param timestamp: the time when this object was created
        """ 
        self.log = logging.getLogger('info')
        self.log.addHandler(logging.NullHandler())

        msg ='Initializing object with input options: \
data={data}, is_raw={is_raw}, is_mutable={is_mutable}, timestamp={timestamp}'
        msg.format(data=data,
                   is_raw=is_raw,
                   is_mutable=is_mutable,
                   timestamp=timestamp)
        self.log.debug(msg)

        self.is_raw = is_raw
        self.is_mutable = is_mutable

        if is_raw and is_mutable and type(data) is not list:
            msg = 'Input data %s is not a list. Raising exception' %data
            self.log.error(msg)
            raise IncorrectInputDataType(list)
        if not is_raw and type(data) is not dict:
            msg = 'Input data %s is not a dict. Raising exception' %data
            self.log.error(msg)
            raise IncorrectInputDataType(dict)
        self.data = data 

        if not timestamp:
            timestamp = int(time.time())
            msg = 'Setting timestamp to %s' %timestamp
            self.log.debug(msg)
        self.timestamp = timestamp

        self.log.debug('Object initialized')

    # -------------------------------------------------------------------------
    # methods to manipulate the data
    # -------------------------------------------------------------------------

    def analyze(self, analyzer):
        """
        generic method that picks the right one 
        based on the type of analyzer
        :param analyzer: an Analyzer object 
        :rtype StatusInfo:
        """
        self.log.debug('Starting')
        if analyzer.analyzertype == 'indexby':
            return self.indexby(analyzer)
        elif analyzer.analyzertype == 'filter':
            return self.filter(analyzer)
        elif analyzer.analyzertype == 'map':
            return self.map(analyzer)
        elif analyzer.analyzertype == 'reduce':
            return self.reduce(analyzer)
        elif analyzer.analyzertype == 'process':
            return self.process(analyzer)
        else:
            msg = 'Input object %s is not a valid analyzer. Raising exception.'
            self.log.error(msg)
            raise NotAnAnalyzer()


    @validate_call
    def indexby(self, analyzer):
        """
        groups the items recorded in self.data into a dictionary
        and creates a new StatusInfo object with it. 
           1. make a dictinary grouping items according to rules in analyzer
           2. convert that dictionary into a dictionary of StatusInfo objects
           3. make a new StatusInfo with that dictionary
        :param analyzer: an object implementing method indexby()
        :rtype StatusInfo:
        """
        self.log.debug('Starting with analyzer %s' %analyzer)

        if self.is_raw:
            self.log.debug('Data is raw')
            # 1
            tmp_new_data = {} 
            for item in self.data:
                key = analyzer.indexby(item)
                if key:
                    if key not in tmp_new_data.keys():
                        tmp_new_data[key] = []
                    tmp_new_data[key].append(item) 
            # 2
            new_data = {}
            for k, v in tmp_new_data.items():
                new_data[k] = StatusInfo(v, timestamp=self.timestamp)
            # 3
            new_info = StatusInfo(new_data, 
                                  is_raw=False, 
                                  timestamp=self.timestamp)
            return new_info
        else:
            self.log.debug('Data is not raw')
            new_data = {}
            for key, statusinfo in self.data.items():
                new_data[key] = statusinfo.indexby(analyzer)
            new_info = StatusInfo(new_data, 
                                  is_raw=False, 
                                  timestamp=self.timestamp)
            return new_info


    @validate_call
    def map(self, analyzer):
        """
        modifies each item in self.data according to rules
        in analyzer
        :param analyzer: an object implementing method map()
        :rtype StatusInfo:
        """
        self.log.debug('Starting with analyzer %s' %analyzer)

        if self.is_raw:
            new_data = []
            for item in self.data:
                new_item = analyzer.map(item)
                new_data.append(new_item)
            new_info = StatusInfo(new_data, timestamp=self.timestamp)
            return new_info
        else:
            new_data = {}
            for key, statusinfo in self.data.items():
                new_data[key] = statusinfo.map(analyzer)
            new_info = StatusInfo(new_data, 
                                  is_raw=False, 
                                  timestamp=self.timestamp)
            return new_info


    @validate_call
    def filter(self, analyzer):
        """
        eliminates the items in self.data that do not pass
        the filter implemented in analyzer
        :param analyzer: an object implementing method filter()
        :rtype StatusInfo:
        """
        self.log.debug('Starting with analyzer %s' %analyzer)

        if self.is_raw:
            new_data = []
            for item in self.data:
                if analyzer.filter(item):
                    new_data.append(item)
            new_info = StatusInfo(new_data, timestamp=self.timestamp)
            return new_info
        else:
            new_data = {}
            for key, statusinfo in self.data.items(): 
                new_data[key] = statusinfo.filter(analyzer)
            new_info = StatusInfo(new_data, 
                                  is_raw=False, 
                                  timestamp=self.timestamp)
            return new_info


    @validate_call
    def reduce(self, analyzer):
        """
        process the entire self.data at the raw level and accumulate values
        :param analyzer: an object implementing method reduce()
        :rtype StatusInfo: 
        """
        self.log.debug('Starting with analyzer %s' %analyzer)
        
        if self.is_raw:
            value = analyzer.init_value
            for item in self.data:
                value = analyzer.reduce(value, item) 
            new_info = StatusInfo(value, 
                                  is_mutable=False, 
                                  timestamp=self.timestamp)
            return new_info
        else:
            new_data = {}
            for key, statusinfo in self.data.items(): 
                new_data[key] = statusinfo.reduce(analyzer)
            new_info = StatusInfo(new_data, 
                                  is_raw=False, 
                                  is_mutable=False, 
                                  timestamp=self.timestamp)
            return new_info


    @validate_call
    def process(self, analyzer):
        """
        process the entire self.data at the raw level
        :param analyzer: an object implementing method process()
        :rtype StatusInfo: 
        """
        self.log.debug('Starting with analyzer %s' %analyzer)

        if self.is_raw:
            new_data = None
            new_data = analyzer.process(self.data)
            new_info = StatusInfo(new_data, 
                                  is_mutable=False, 
                                  timestamp=self.timestamp)
            return new_info
        else:
            new_data = {}
            for key, statusinfo in self.data.items(): 
                new_data[key] = statusinfo.process(analyzer)
            new_info = StatusInfo(new_data, 
                                  is_raw=False, 
                                  is_mutable=False, 
                                  timestamp=self.timestamp)
            return new_info


    # -------------------------------------------------------------------------
    # method to get the data
    # -------------------------------------------------------------------------

    def getraw(self):
        """
        returns the structure of all raw data components
        :rtype composed data:
        """
        if self.is_raw:
            return self.data
        else:
            out = {}
            for key, value in self.data.items():
                out[key] = value.getraw()
            return out


    def get(self, *key_l):
        """
        returns the data hosted by the Info object in the tree structure pointed 
        by all keys
        The output is the data, either a dictionary or the original raw list 
        :param key_l list: list of keys for each nested dictionary
        :rtype data:
        """
        if len(key_l) == 0:
            return self.data
        else:
            key = key_l[0]
            if key not in self.data.keys():
                raise MissingKey(key)
            data = self.data[key]
            return data.get(*key_l[1:])
            

    def __getitem__(self, key):
        """
        returns the Info object pointed by the key
        :param key: the key in the higher level dictionary
        :rtype StatusInfo: 
        """
        if self.is_raw:
            raise IsRawData(key)
        if key not in self.data.keys():
            raise MissingKey(key)
        return self.data[key]




# =============================================================================
# Analyzers
# =============================================================================

class Analyzer(object):
    pass


class AnalyzerIndexBy(Analyzer):
    analyzertype = "indexby"
    def indexby(self):
        raise NotImplementedError


class AnalyzerFilter(Analyzer):
    analyzertype = "filter"
    def filter(self):
        raise NotImplementedError


class AnalyzerMap(Analyzer):
    analyzertype = "map"
    def map(self):
        raise NotImplementedError


class AnalyzerReduce(Analyzer):
    analyzertype = "reduce"
    def __init__(self, init_value=None):
        self.init_value = init_value
    def reduce(self):
        raise NotImplementedError


class AnalyzerProcess(Analyzer):
    analyzertype = "process"
    def process(self):
        raise NotImplementedError


class Algorithm(object):
    """
    container for multiple Analyzer objects
    """
    def __init__(self):
        self.analyzer_l= []

    def add(self, analyzer):
        self.analyzer_l.append(analyzer)

    def analyze(self, input_data):
        tmp_out = input_data
        for analyzer in self.analyzer_l:
            tmp_out = tmp_out.analyze(analyzer)
        return tmp_out



# =============================================================================
#  Some basic Analyzers
# =============================================================================

class GroupByKey(AnalyzerIndexBy):

    def __init__(self, key):
        self.key = key

    def indexby(self, job):
        try:
            return job[self.key]
        except Exception, ex:
            return None


class GroupByKeyRemap(AnalyzerIndexBy):

    def __init__(self, key, mapping_d):
        self.key = key
        self.mapping_d = mapping_d

    def indexby(self, job):
        try:
            value = str(job[self.key])
        except Exception, ex:
            return None

        if value in self.mapping_d.keys():
            return self.mapping_d[value]
        else:
            return None


class AttributeValue(AnalyzerFilter):

    def __init__(self, attribute, value):
        self.attribute = attribute 
        self.value = value

    def filter(self, job):
        if self.attribute not in job.keys():
            msg = 'job {job} does not have key {key}.'
            msg = msg.format(job=job, 
                             key=self.attribute)
            logmsg = msg + ' Raising Exception.'
            self.log.error(logmsg)
            raise AnalyzerFailure(msg)
        return job[self.attribute] == self.value


class Count(AnalyzerProcess):

    def process(self, data):
        return len(data)


class TotalRunningTime(AnalyzerReduce):

    def __init__(self):
        self.now = int(time.time())
        super(TotalRunningTime, self).__init__(0)

    def reduce(self, value, job):
        running = self.now - int(job['enteredcurrentstatus'])
        if value:
            running += value
        return running


# FIXME: need a better name for this class
class TotalRunningTime2(AnalyzerReduce):

    def __init__(self):
        self.now = int(time.time())
        super(TotalRunningTime2, self).__init__(0)

    def reduce(self, value, job):
        if job['jobstatus'] == 2:
            running = self.now - int(job['enteredcurrentstatus'])
        elif job['jobstatus'] == 3 or \
             job['jobstatus'] == 4:
            running = int(job['remotewallclocktime'])
        else:
            running = 0

        if value:
            running += value
        return running



# =============================================================================
# Exceptions
# =============================================================================

class IncorrectInputDataType(Exception):
    def __init__(self, type):
        self.value = 'Type of input data is not %s' %type
    def __str__(self):
        return repr(self.value)


class NotAnAnalyzer(Exception):
    def __init__(self):
        self.value = 'object does not have a valid analyzertype value'
    def __str__(self):
        return repr(self.value)


class IncorrectAnalyzer(Exception):
    def __init__(self, analyzer, analyzertype, methodname):
        value = "Analyzer object {ana} is of type '{atype}' but used for '{call}()'" 
        self.value = value.format(ana=analyzer, 
                                  atype=analyzertype, 
                                  call=methodname)
    def __str__(self):
        return repr(self.value)


class MissingKey(Exception):
    def __init__(self, key):
        self.value = "Key %s is not in the data dictionary" %key
    def __str__(self):
        return repr(self.value)


class IsRawData(Exception):
    def __init__(self, key):
        self.value = "Info object is raw. It does not have key %s" %key
    def __str__(self):
        return repr(self.value)


class ObjectIsNotMutable(Exception):
    def __init__(self, method):
        self.value = "object is not mutable, method %s can not be invoked anymore" %method
    def __str__(self):
        return repr(self.value)


class AnalyzerFailure(Exception):
    """
    generic Exception for any unclassified failure
    """
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)

