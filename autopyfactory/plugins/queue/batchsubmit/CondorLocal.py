#!/bin/env python
#
# AutoPyfactory batch plugin for Condor
#

from CondorBase import CondorBase 
from autopyfactory import jsd


class CondorLocal(CondorBase):
    id = 'condorlocal'
    '''
    This class is expected to have separate instances for each PandaQueue object. 
    '''
    
    def __init__(self, apfqueue, config=None):
        
        if not config:
            qcl = apfqueue.qcl            
        else:
            qcl = config
        newqcl = qcl.clone().filterkeys('batchsubmit.condorlocal', 'batchsubmit.condorbase')

        super(CondorLocal, self).__init__(apfqueue, config=newqcl) 
        self.log.info('CondorLocal: Object initialized.')

       
    def _addJSD(self):
        '''
        add things to the JSD object
        '''

        self.log.debug('CondorLocal.addJSD: Starting.')

        self.JSD.add("universe", "vanilla")
        self.JSD.add("should_transfer_files", "IF_NEEDED")
        self.JSD.add('+TransferOutput', '""')

        super(CondorLocal, self)._addJSD()

        self.log.debug('CondorLocal.addJSD: Leaving.')
    