# Copyright 2010 OpenStack LLC.
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


import stubout
import webob
import json

import nova.db
from nova import context
from nova import flags
from nova import test
from nova.api.openstack import zones
from nova.tests.api.openstack import fakes


FLAGS = flags.FLAGS
FLAGS.verbose = True


def zone_get(context, zone_id):
    return dict(id=1, api_url='http://foo.com', username='bob',
                password='xxx')


def zone_create(context, values):
    zone = dict(id=1)
    zone.update(values)
    return zone


def zone_update(context, zone_id, values):
    zone = dict(id=zone_id, api_url='http://foo.com', username='bob',
                password='xxx')
    zone.update(values)
    return zone


def zone_delete(context, zone_id):
    pass


def zone_get_all(context):
    return [
        dict(id=1, api_url='http://foo.com', username='bob',
                 password='xxx'),
        dict(id=2, api_url='http://blah.com', username='alice',
                 password='qwerty')]


class ZonesTest(test.TestCase):
    def setUp(self):
        super(ZonesTest, self).setUp()
        self.stubs = stubout.StubOutForTesting()
        fakes.FakeAuthManager.auth_data = {}
        fakes.FakeAuthDatabase.data = {}
        fakes.stub_out_networking(self.stubs)
        fakes.stub_out_rate_limiting(self.stubs)
        fakes.stub_out_auth(self.stubs)

        self.allow_admin = FLAGS.allow_admin_api
        FLAGS.allow_admin_api = True

        self.stubs.Set(nova.db, 'zone_get', zone_get)
        self.stubs.Set(nova.db, 'zone_get_all', zone_get_all)
        self.stubs.Set(nova.db, 'zone_update', zone_update)
        self.stubs.Set(nova.db, 'zone_create', zone_create)
        self.stubs.Set(nova.db, 'zone_delete', zone_delete)

    def tearDown(self):
        self.stubs.UnsetAll()
        FLAGS.allow_admin_api = self.allow_admin
        super(ZonesTest, self).tearDown()

    def test_get_zone_list(self):
        req = webob.Request.blank('/v1.0/zones')
        res = req.get_response(fakes.wsgi_app())
        res_dict = json.loads(res.body)

        self.assertEqual(res.status_int, 200)
        self.assertEqual(len(res_dict['zones']), 2)

    def test_get_zone_by_id(self):
        req = webob.Request.blank('/v1.0/zones/1')
        res = req.get_response(fakes.wsgi_app())
        res_dict = json.loads(res.body)

        self.assertEqual(res_dict['zone']['id'], 1)
        self.assertEqual(res_dict['zone']['api_url'], 'http://foo.com')
        self.assertFalse('password' in res_dict['zone'])
        self.assertEqual(res.status_int, 200)

    def test_zone_delete(self):
        req = webob.Request.blank('/v1.0/zones/1')
        res = req.get_response(fakes.wsgi_app())

        self.assertEqual(res.status_int, 200)

    def test_zone_create(self):
        body = dict(zone=dict(api_url='http://blah.zoo', username='fred',
                        password='fubar'))
        req = webob.Request.blank('/v1.0/zones')
        req.method = 'POST'
        req.body = json.dumps(body)

        res = req.get_response(fakes.wsgi_app())
        res_dict = json.loads(res.body)

        self.assertEqual(res.status_int, 200)
        self.assertEqual(res_dict['zone']['id'], 1)
        self.assertEqual(res_dict['zone']['api_url'], 'http://blah.zoo')
        self.assertFalse('username' in res_dict['zone'])

    def test_zone_update(self):
        body = dict(zone=dict(username='zeb', password='sneaky'))
        req = webob.Request.blank('/v1.0/zones/1')
        req.method = 'PUT'
        req.body = json.dumps(body)

        res = req.get_response(fakes.wsgi_app())
        res_dict = json.loads(res.body)

        self.assertEqual(res.status_int, 200)
        self.assertEqual(res_dict['zone']['id'], 1)
        self.assertEqual(res_dict['zone']['api_url'], 'http://foo.com')
        self.assertFalse('username' in res_dict['zone'])
