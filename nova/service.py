# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
Generic Node baseclass for all workers that run on hosts
"""

import inspect
import os
import sys
import time

from eventlet import event
from eventlet import greenthread
from eventlet import greenpool

from sqlalchemy.exc import OperationalError

from nova import context
from nova import db
from nova import exception
from nova import log as logging
from nova import flags
from nova import rpc
from nova import utils
from nova import version


FLAGS = flags.FLAGS
flags.DEFINE_integer('report_interval', 10,
                     'seconds between nodes reporting state to datastore',
                     lower_bound=1)
flags.DEFINE_integer('periodic_interval', 60,
                     'seconds between running periodic tasks',
                     lower_bound=1)


class Service(object):
    """Base class for workers that run on hosts."""

    def __init__(self, host, binary, topic, manager, report_interval=None,
                 periodic_interval=None, *args, **kwargs):
        self.host = host
        self.binary = binary
        self.topic = topic
        self.manager_class_name = manager
        manager_class = utils.import_class(self.manager_class_name)
        self.manager = manager_class(host=self.host, *args, **kwargs)
        self.report_interval = report_interval
        self.periodic_interval = periodic_interval
        super(Service, self).__init__(*args, **kwargs)
        self.saved_args, self.saved_kwargs = args, kwargs
        self.timers = []

    def start(self):
        vcs_string = version.version_string_with_vcs()
        logging.audit(_("Starting %(topic)s node (version %(vcs_string)s)"),
                      {'topic': self.topic, 'vcs_string': vcs_string})
        self.manager.init_host()
        self.model_disconnected = False
        ctxt = context.get_admin_context()
        try:
            service_ref = db.service_get_by_args(ctxt,
                                                 self.host,
                                                 self.binary)
            self.service_id = service_ref['id']
        except exception.NotFound:
            self._create_service_ref(ctxt)

        conn1 = rpc.Connection.instance(new=True)
        conn2 = rpc.Connection.instance(new=True)
        if self.report_interval:
            consumer_all = rpc.AdapterConsumer(
                    connection=conn1,
                    topic=self.topic,
                    proxy=self)
            consumer_node = rpc.AdapterConsumer(
                    connection=conn2,
                    topic='%s.%s' % (self.topic, self.host),
                    proxy=self)

            self.timers.append(consumer_all.attach_to_eventlet())
            self.timers.append(consumer_node.attach_to_eventlet())

            pulse = utils.LoopingCall(self.report_state)
            pulse.start(interval=self.report_interval, now=False)
            self.timers.append(pulse)

        if self.periodic_interval:
            periodic = utils.LoopingCall(self.periodic_tasks)
            periodic.start(interval=self.periodic_interval, now=False)
            self.timers.append(periodic)

    def _create_service_ref(self, context):
        zone = FLAGS.node_availability_zone
        service_ref = db.service_create(context,
                                        {'host': self.host,
                                         'binary': self.binary,
                                         'topic': self.topic,
                                         'report_count': 0,
                                         'availability_zone': zone})
        self.service_id = service_ref['id']

    def __getattr__(self, key):
        manager = self.__dict__.get('manager', None)
        return getattr(manager, key)

    @classmethod
    def create(cls,
               host=None,
               binary=None,
               topic=None,
               manager=None,
               report_interval=None,
               periodic_interval=None):
        """Instantiates class and passes back application object.

        Args:
            host, defaults to FLAGS.host
            binary, defaults to basename of executable
            topic, defaults to bin_name - "nova-" part
            manager, defaults to FLAGS.<topic>_manager
            report_interval, defaults to FLAGS.report_interval
            periodic_interval, defaults to FLAGS.periodic_interval
        """
        if not host:
            host = FLAGS.host
        if not binary:
            binary = os.path.basename(inspect.stack()[-1][1])
        if not topic:
            topic = binary.rpartition("nova-")[2]
        if not manager:
            manager = FLAGS.get('%s_manager' % topic, None)
        if not report_interval:
            report_interval = FLAGS.report_interval
        if not periodic_interval:
            periodic_interval = FLAGS.periodic_interval
        service_obj = cls(host, binary, topic, manager,
                          report_interval, periodic_interval)

        return service_obj

    def kill(self):
        """Destroy the service object in the datastore"""
        self.stop()
        try:
            db.service_destroy(context.get_admin_context(), self.service_id)
        except exception.NotFound:
            logging.warn(_("Service killed that has no database entry"))

    def stop(self):
        for x in self.timers:
            try:
                x.stop()
            except Exception:
                pass
        self.timers = []

    def wait(self):
        for x in self.timers:
            try:
                x.wait()
            except Exception:
                pass

    def periodic_tasks(self):
        """Tasks to be run at a periodic interval"""
        self.manager.periodic_tasks(context.get_admin_context())

    def report_state(self):
        """Update the state of this service in the datastore."""
        ctxt = context.get_admin_context()
        try:
            try:
                service_ref = db.service_get(ctxt, self.service_id)
            except exception.NotFound:
                logging.debug(_("The service database object disappeared, "
                                "Recreating it."))
                self._create_service_ref(ctxt)
                service_ref = db.service_get(ctxt, self.service_id)

            db.service_update(ctxt,
                             self.service_id,
                             {'report_count': service_ref['report_count'] + 1})

            # TODO(termie): make this pattern be more elegant.
            if getattr(self, "model_disconnected", False):
                self.model_disconnected = False
                logging.error(_("Recovered model server connection!"))

        # TODO(vish): this should probably only catch connection errors
        except Exception:  # pylint: disable-msg=W0702
            if not getattr(self, "model_disconnected", False):
                self.model_disconnected = True
                logging.exception(_("model server went away"))


def serve(*services):
    try:
        if not services:
            services = [Service.create()]
    except Exception:
        logging.exception('in Service.create()')
        raise
    finally:
        # After we've loaded up all our dynamic bits, check
        # whether we should print help
        flags.DEFINE_flag(flags.HelpFlag())
        flags.DEFINE_flag(flags.HelpshortFlag())
        flags.DEFINE_flag(flags.HelpXMLFlag())
        FLAGS.ParseNewFlags()

    name = '_'.join(x.binary for x in services)
    logging.debug(_("Serving %s"), name)
    logging.debug(_("Full set of FLAGS:"))
    for flag in FLAGS:
        flag_get = FLAGS.get(flag, None)
        logging.debug("%(flag)s : %(flag_get)s" % locals())

    for x in services:
        x.start()


def wait():
    while True:
        greenthread.sleep(5)
