# coding=utf8
"""
Run Python test suite via the standard unittest mechanism.
Usage:
  python test.py
  python test.py --logall
  python test.py TestConformTransforms
  python test.py -l TestOA.test_process
All logging is suppressed unless --logall or -l specified
~/.openaddr-logging-test.json can also be used to configure log behavior
"""


from __future__ import absolute_import, division, print_function

import unittest
import shutil
import tempfile
import json
import re
import pickle
import sys
import os
import csv
import logging
from os import close, environ, mkdir, remove
from io import BytesIO
from csv import DictReader
from itertools import cycle
from zipfile import ZipFile
from datetime import datetime, timedelta
from mimetypes import guess_type
from urllib.parse import urlparse, parse_qs
from os.path import dirname, join, basename, exists, splitext
from contextlib import contextmanager
from subprocess import Popen, PIPE
from unicodedata import normalize
from threading import Lock

if sys.platform != 'win32':
    from fcntl import lockf, LOCK_EX, LOCK_UN
else:
    lockf, LOCK_EX, LOCK_UN = None, None, None

from requests import get
from httmock import response, HTTMock
import mock

from .. import cache, conform, process_one
from ..util import package_output
from ..cache import CacheResult
from ..conform import ConformResult
from ..process_one import find_source_problem, SourceProblem

def touch_first_arg_file(path, *args, **kwargs):
    ''' Write a short dummy file for the first argument.
    '''
    with open(path, 'w') as file:
        file.write('yo')

def touch_second_arg_file(_, path, *args, **kwargs):
    ''' Write a short dummy file for the second argument.
    '''
    with open(path, 'w') as file:
        file.write('yo')

class TestOA (unittest.TestCase):

    def setUp(self):
        ''' Prepare a clean temporary directory, and copy sources there.
        '''
        self.testdir = tempfile.mkdtemp(prefix='testOA-')
        self.src_dir = join(self.testdir, 'sources')
        sources_dir = join(dirname(__file__), 'sources')
        shutil.copytree(sources_dir, self.src_dir)

    def tearDown(self):
        shutil.rmtree(self.testdir)

    def response_content(self, url, request):
        ''' Fake HTTP responses for use with HTTMock in tests.
        '''
        scheme, host, path, _, query, _ = urlparse(url.geturl())
        data_dirname = join(dirname(__file__), 'data')
        local_path = None

        if host == 'fake-s3.local':
            return response(200, self.s3._read_fake_key(path))

        if (host, path) == ('data.acgov.org', '/api/geospatial/8e4s-7f4v'):
            local_path = join(data_dirname, 'us-ca-alameda_county-excerpt.zip')

        if (host, path) == ('data.acgov.org', '/api/geospatial/MiXeD-cAsE'):
            local_path = join(data_dirname, 'us-ca-alameda_county-excerpt-mixedcase.zip')

        if (host, path) == ('www.ci.berkeley.ca.us', '/uploadedFiles/IT/GIS/Parcels.zip'):
            local_path = join(data_dirname, 'us-ca-berkeley-excerpt.zip')

        if (host, path) == ('www.ci.berkeley.ca.us', '/uploadedFiles/IT/GIS/No-Parcels.zip'):
            return response(404, 'Nobody here but us coats')

        if (host, path) == ('www.dropbox.com', '/s/fhopgbg4vkyoobr/czech_addresses_wgs84_12092016_MASTER.zip'):
            return response(404, 'Nobody here but us coats')

        if (host, path) == ('data.openaddresses.io', '/cache/uploads/migurski/d5add2/oregon_state_addresses.zip'):
            return response(404, 'Nobody here but us coats')

        if (host, path) == ('data.openoakland.org', '/sites/default/files/OakParcelsGeo2013_0.zip'):
            local_path = join(data_dirname, 'us-ca-oakland-excerpt.zip')

        if (host, path) == ('data.openaddresses.io', '/cache/pl.zip'):
            local_path = join(data_dirname, 'pl.zip')

        if (host, path) == ('data.openaddresses.io', '/cache/jp-fukushima.zip'):
            local_path = join(data_dirname, 'jp-fukushima.zip')

        if (host, path) == ('data.sfgov.org', '/download/kvej-w5kb/ZIPPED%20SHAPEFILE'):
            local_path = join(data_dirname, 'us-ca-san_francisco-excerpt.zip')

        if (host, path) == ('ftp.vgingis.com', '/Download/VA_SiteAddress.txt.zip'):
            local_path = join(data_dirname, 'VA_SiteAddress-excerpt.zip')

        if (host, path) == ('gis3.oit.ohio.gov', '/LBRS/_downloads/TRU_ADDS.zip'):
            local_path = join(data_dirname, 'TRU_ADDS-excerpt.zip')

        if (host, path) == ('data.openaddresses.io', '/cache/uploads/iandees/ed482f/bucks.geojson.zip'):
            local_path = join(data_dirname, 'us-pa-bucks.geojson.zip')

        if (host, path) == ('www.carsonproperty.info', '/ArcGIS/rest/services/basemap/MapServer/1/query'):
            qs = parse_qs(query)
            body_data = parse_qs(request.body) if request.body else {}

            if qs.get('returnIdsOnly') == ['true']:
                local_path = join(data_dirname, 'us-ca-carson-ids-only.json')
            elif qs.get('returnCountOnly') == ['true']:
                local_path = join(data_dirname, 'us-ca-carson-count-only.json')
            elif body_data.get('outSR') == ['4326']:
                local_path = join(data_dirname, 'us-ca-carson-0.json')

        if (host, path) == ('www.carsonproperty.info', '/ArcGIS/rest/services/basemap/MapServer/1'):
            qs = parse_qs(query)

            if qs.get('f') == ['json']:
                local_path = join(data_dirname, 'us-ca-carson-metadata.json')

        if (host, path) == ('72.205.198.131', '/ArcGIS/rest/services/Brown/Brown/MapServer/33/query'):
            qs = parse_qs(query)
            body_data = parse_qs(request.body) if request.body else {}

            if qs.get('returnIdsOnly') == ['true']:
                local_path = join(data_dirname, 'us-ks-brown-ids-only.json')
            elif qs.get('returnCountOnly') == ['true']:
                local_path = join(data_dirname, 'us-ks-brown-count-only.json')
            elif body_data.get('outSR') == ['4326']:
                local_path = join(data_dirname, 'us-ks-brown-0.json')

        if (host, path) == ('72.205.198.131', '/ArcGIS/rest/services/Brown/Brown/MapServer/33'):
            qs = parse_qs(query)

            if qs.get('f') == ['json']:
                local_path = join(data_dirname, 'us-ks-brown-metadata.json')

        if (host, path) == ('services1.arcgis.com', '/I6XnrlnguPDoEObn/arcgis/rest/services/AddressPoints/FeatureServer/0/query'):
            qs = parse_qs(query)
            body_data = parse_qs(request.body) if request.body else {}

            if qs.get('returnCountOnly') == ['true']:
                local_path = join(data_dirname, 'us-pa-lancaster-count-only.json')
            elif body_data.get('outSR') == ['4326']:
                local_path = join(data_dirname, 'us-pa-lancaster-0.json')
            elif body_data.get('resultRecordCount') == ['1']:
                local_path = join(data_dirname, 'us-pa-lancaster-probe.json')

        if (host, path) == ('services1.arcgis.com', '/I6XnrlnguPDoEObn/arcgis/rest/services/AddressPoints/FeatureServer/0'):
            qs = parse_qs(query)

            if qs.get('f') == ['json']:
                local_path = join(data_dirname, 'us-pa-lancaster-metadata.json')

        if (host, path) == ('services.geoportalmaps.com', '/arcgis/rest/services/Runnels_Services/MapServer/1'):
            qs = parse_qs(query)

            if qs.get('f') == ['json']:
                local_path = join(data_dirname, 'us-tx-runnels-metadata.json')

        if (host, path) == ('maps.co.washington.mn.us', '/arcgis/rest/services/Public/Public_Parcels/MapServer/0/query'):
            qs = parse_qs(query)
            body_data = parse_qs(request.body) if request.body else {}

            if qs.get('returnCountOnly') == ['true']:
                local_path = join(data_dirname, 'us-nm-washington-count-only.json')
            elif body_data.get('outSR') == ['4326']:
                local_path = join(data_dirname, 'us-nm-washington-0.json')
            elif body_data.get('resultRecordCount') == ['1']:
                local_path = join(data_dirname, 'us-nm-washington-probe.json')

        if (host, path) == ('maps.co.washington.mn.us', '/arcgis/rest/services/Public/Public_Parcels/MapServer/0'):
            qs = parse_qs(query)

            if qs.get('f') == ['json']:
                local_path = join(data_dirname, 'us-nm-washington-metadata.json')

        if (host, path) == ('gis.ci.waco.tx.us', '/arcgis/rest/services/Parcels/MapServer/0/query'):
            qs = parse_qs(query)
            body_data = parse_qs(request.body) if request.body else {}

            if qs.get('returnCountOnly') == ['true']:
                local_path = join(data_dirname, 'us-tx-waco-count-only.json')
            elif body_data.get('outSR') == ['4326']:
                local_path = join(data_dirname, 'us-tx-waco-0.json')

        if (host, path) == ('gis.ci.waco.tx.us', '/arcgis/rest/services/Parcels/MapServer/0'):
            qs = parse_qs(query)

            if qs.get('f') == ['json']:
                local_path = join(data_dirname, 'us-tx-waco-metadata.json')

        if (host, path) == ('ocgis.orangecountygov.com', '/ArcGIS/rest/services/Dynamic/LandBase/MapServer/0/query'):
            qs = parse_qs(query)
            body_data = parse_qs(request.body) if request.body else {}

            if qs.get('returnIdsOnly') == ['true']:
                local_path = join(data_dirname, 'us-ny-orange-ids-only.json')
            elif qs.get('returnCountOnly') == ['true']:
                local_path = join(data_dirname, 'us-ny-orange-count-only.json')
            elif body_data.get('outSR') == ['4326']:
                local_path = join(data_dirname, 'us-ny-orange-0.json')

        if (host, path) == ('ocgis.orangecountygov.com', '/ArcGIS/rest/services/Dynamic/LandBase/MapServer/0'):
            qs = parse_qs(query)

            if qs.get('f') == ['json']:
                local_path = join(data_dirname, 'us-ny-orange-metadata.json')

        if (host, path) == ('cdr.citynet.kharkov.ua', '/arcgis/rest/services/gis_ort_stat_general/MapServer/1/query'):
            qs = parse_qs(query)
            body_data = parse_qs(request.body) if request.body else {}

            if qs.get('returnIdsOnly') == ['true']:
                local_path = join(data_dirname, 'ua-kharkiv-ids-only.json')
            if qs.get('returnCountOnly') == ['true']:
                local_path = join(data_dirname, 'ua-kharkiv-count-only.json')
            elif 'outStatistics' in qs:
                local_path = join(data_dirname, 'ua-kharkiv-statistics.json')
            elif body_data.get('outSR') == ['4326']:
                local_path = join(data_dirname, 'ua-kharkiv-0.json')

        if (host, path) == ('cdr.citynet.kharkov.ua', '/arcgis/rest/services/gis_ort_stat_general/MapServer/1'):
            qs = parse_qs(query)

            if qs.get('f') == ['json']:
                local_path = join(data_dirname, 'ua-kharkiv-metadata.json')

        if (host, path) == ('data.openaddresses.io', '/20000101/us-ca-carson-cached.json'):
            local_path = join(data_dirname, 'us-ca-carson-cache.geojson')

        if (host, path) == ('data.openaddresses.io', '/cache/fr/BAN_licence_gratuite_repartage_75.zip'):
            local_path = join(data_dirname, 'BAN_licence_gratuite_repartage_75.zip')

        if (host, path) == ('data.openaddresses.io', '/cache/fr/BAN_licence_gratuite_repartage_974.zip'):
            local_path = join(data_dirname, 'BAN_licence_gratuite_repartage_974.zip')

        if (host, path) == ('fbarc.stadt-berlin.de', '/FIS_Broker_Atom/Hauskoordinaten/HKO_EPSG3068.zip'):
            local_path = join(data_dirname, 'de-berlin-excerpt.zip')

        if (host, path) == ('www.dropbox.com', '/s/8uaqry2w657p44n/bagadres.zip'):
            local_path = join(data_dirname, 'nl.zip')

        if (host, path) == ('s.irisnet.be', '/v1/AUTH_b4e6bcc3-db61-442e-8b59-e0ce9142d182/Region/UrbAdm_SHP.zip'):
            local_path = join(data_dirname, 'be-wa-brussels.zip')

        if (host, path) == ('data.openaddresses.io', '/cache/uploads/migurski/ed789f/toscana20160804.zip'):
            local_path = join(data_dirname, 'it-52-statewide.zip')

        if (host, path) == ('data.openaddresses.io', '/cache/uploads/nvkelso/5a5bf6/ParkCountyADDRESS_POINTS_point.zip'):
            local_path = join(data_dirname, 'us-wy-park.zip')

        if (host, path) == ('njgin.state.nj.us', '/download2/Address/ADDR_POINT_NJ_fgdb.zip'):
            local_path = join(data_dirname, 'nj-statewide.gdb.zip')

        if (host, path) == ('data.openaddresses.io', '/cache/uploads/trescube/f5df2e/us-mi-grand-traverse.geojson.zip'):
            local_path = join(data_dirname, 'us-mi-grand-traverse.geojson.zip')

        if (host, path) == ('fake-web', '/lake-man.gdb.zip'):
            local_path = join(data_dirname, 'lake-man.gdb.zip')

        if (host, path) == ('fake-web', '/lake-man-gdb-othername.zip'):
            local_path = join(data_dirname, 'lake-man-gdb-othername.zip')

        if (host, path) == ('fake-web', '/lake-man-gdb-othername-nodir.zip'):
            local_path = join(data_dirname, 'lake-man-gdb-othername-nodir.zip')

        if scheme == 'file':
            local_path = path

        if local_path:
            type, _ = guess_type(local_path)
            with open(local_path, 'rb') as file:
                return response(200, file.read(), headers={'Content-Type': type})

        raise NotImplementedError(url.geturl())

    def response_content_ftp(self, url):
        ''' Fake FTP responses for use with mock.patch in tests.
        '''
        scheme, host, path, _, _, _ = urlparse(url)
        data_dirname = join(dirname(__file__), 'data')
        local_path = None

        if scheme != 'ftp':
            raise ValueError("Don't know how to {}".format(scheme))

        if (host, path) == ('ftp.agrc.utah.gov', '/UtahSGID_Vector/UTM12_NAD83/LOCATION/UnpackagedData/AddressPoints/_Statewide/AddressPoints_shp.zip'):
            local_path = join(data_dirname, 'us-ut-excerpt.zip')

        if (host, path) == ('ftp02.portlandoregon.gov', '/CivicApps/address.zip'):
            local_path = join(data_dirname, 'us-or-portland.zip')

        if (host, path) == ('ftp.skra.is', '/skra/STADFANG.dsv.zip'):
            local_path = join(data_dirname, 'iceland.zip')

        if local_path:
            type, _ = guess_type(local_path)
            with open(local_path, 'rb') as file:
                return response(200, file.read(), headers={'Content-Type': type})

        raise NotImplementedError(url)

    def test_single_ac_local(self):
        ''' Test complete process_one.process on Alameda County sample data with a local filepath
        '''
        data_dirname = join(dirname(__file__), 'data')
        local_path = join(data_dirname, 'us-ca-alameda_county-excerpt.zip')
        shutil.copy(local_path, '/tmp/us-ca-alameda.zip')
        source = join(self.src_dir, 'us-ca-alameda_county-local.json')

        with HTTMock(self.response_content), \
             mock.patch('openaddr.preview.render') as preview_ren, \
             mock.patch('openaddr.slippymap.generate') as slippymap_gen:
            preview_ren.side_effect = touch_second_arg_file
            slippymap_gen.side_effect = touch_first_arg_file
            state_path = process_one.process(source, self.testdir, "addresses", "default", True, mapbox_key='mapbox-XXXX')

        self.assertTrue(slippymap_gen.mock_calls[0][1][0].endswith('.mbtiles'))
        self.assertTrue(slippymap_gen.mock_calls[0][1][1].endswith('.csv'))

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['cache'])
        self.assertIsNotNone(state['processed'])
        self.assertIsNotNone(state['sample'])
        self.assertIsNotNone(state['preview'])
        self.assertIsNotNone(state['slippymap'])
        self.assertEqual(state['geometry type'], 'Point')
        self.assertIsNone(state['website'])
        self.assertEqual(state['license'], 'http://www.acgov.org/acdata/terms.htm')
        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertTrue('ZIPCODE' in sample_data[0])
        self.assertTrue('OAKLAND' in sample_data[1])
        self.assertTrue('94612' in sample_data[1])

        output_path = join(dirname(state_path), state['processed'])
        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(rows[1]['ID'], '')
            self.assertEqual(rows[10]['ID'], '')
            self.assertEqual(rows[100]['ID'], '')
            self.assertEqual(rows[1000]['ID'], '')
            self.assertEqual(rows[1]['NUMBER'], '2147')
            self.assertEqual(rows[10]['NUMBER'], '605')
            self.assertEqual(rows[100]['NUMBER'], '167')
            self.assertEqual(rows[1000]['NUMBER'], '322')
            self.assertEqual(rows[1]['STREET'], 'BROADWAY')
            self.assertEqual(rows[10]['STREET'], 'HILLSBOROUGH ST')
            self.assertEqual(rows[100]['STREET'], '8TH ST')
            self.assertEqual(rows[1000]['STREET'], 'HANOVER AV')
            self.assertEqual(rows[1]['UNIT'], '')
            self.assertEqual(rows[10]['UNIT'], '')
            self.assertEqual(rows[100]['UNIT'], '')
            self.assertEqual(rows[1000]['UNIT'], '')

    def test_single_ac(self):
        ''' Test complete process_one.process on Alameda County sample data.
        '''
        source = join(self.src_dir, 'us-ca-alameda_county.json')

        with HTTMock(self.response_content), \
             mock.patch('openaddr.preview.render') as preview_ren, \
             mock.patch('openaddr.slippymap.generate') as slippymap_gen:
            preview_ren.side_effect = touch_second_arg_file
            slippymap_gen.side_effect = touch_first_arg_file
            state_path = process_one.process(source, self.testdir, "addresses", "default", True, mapbox_key='mapbox-XXXX')

        self.assertTrue(slippymap_gen.mock_calls[0][1][0].endswith('.mbtiles'))
        self.assertTrue(slippymap_gen.mock_calls[0][1][1].endswith('.csv'))

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['cache'])
        self.assertIsNotNone(state['processed'])
        self.assertIsNotNone(state['sample'])
        self.assertIsNotNone(state['preview'])
        self.assertIsNotNone(state['slippymap'])
        self.assertEqual(state['geometry type'], 'Point')
        self.assertIsNone(state['website'])
        self.assertEqual(state['license'], 'http://www.acgov.org/acdata/terms.htm')
        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertTrue('ZIPCODE' in sample_data[0])
        self.assertTrue('OAKLAND' in sample_data[1])
        self.assertTrue('94612' in sample_data[1])

        output_path = join(dirname(state_path), state['processed'])
        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(rows[1]['ID'], '')
            self.assertEqual(rows[10]['ID'], '')
            self.assertEqual(rows[100]['ID'], '')
            self.assertEqual(rows[1000]['ID'], '')
            self.assertEqual(rows[1]['NUMBER'], '2147')
            self.assertEqual(rows[10]['NUMBER'], '605')
            self.assertEqual(rows[100]['NUMBER'], '167')
            self.assertEqual(rows[1000]['NUMBER'], '322')
            self.assertEqual(rows[1]['STREET'], 'BROADWAY')
            self.assertEqual(rows[10]['STREET'], 'HILLSBOROUGH ST')
            self.assertEqual(rows[100]['STREET'], '8TH ST')
            self.assertEqual(rows[1000]['STREET'], 'HANOVER AV')
            self.assertEqual(rows[1]['UNIT'], '')
            self.assertEqual(rows[10]['UNIT'], '')
            self.assertEqual(rows[100]['UNIT'], '')
            self.assertEqual(rows[1000]['UNIT'], '')

    def test_single_ac_mixedcase(self):
        ''' Test complete process_one.process on Alameda County sample data.
        '''
        source = join(self.src_dir, 'us-ca-alameda_county-mixedcase.json')

        with HTTMock(self.response_content), \
             mock.patch('openaddr.preview.render') as preview_ren, \
             mock.patch('openaddr.slippymap.generate') as slippymap_gen:
            preview_ren.side_effect = touch_second_arg_file
            slippymap_gen.side_effect = touch_first_arg_file
            state_path = process_one.process(source, self.testdir, "addresses", "default", True, mapbox_key='mapbox-XXXX')

        self.assertTrue(slippymap_gen.mock_calls[0][1][0].endswith('.mbtiles'))
        self.assertTrue(slippymap_gen.mock_calls[0][1][1].endswith('.csv'))

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['cache'])
        self.assertIsNotNone(state['processed'])
        self.assertIsNotNone(state['sample'])
        self.assertIsNotNone(state['preview'])
        self.assertIsNotNone(state['slippymap'])
        self.assertEqual(state['geometry type'], 'Point')
        self.assertIsNone(state['website'])
        self.assertEqual(state['license'], 'http://www.acgov.org/acdata/terms.htm')

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertTrue('ZIPCODE' in sample_data[0])
        self.assertTrue('OAKLAND' in sample_data[1])
        self.assertTrue('94612' in sample_data[1])

        output_path = join(dirname(state_path), state['processed'])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(rows[1]['ID'], '')
            self.assertEqual(rows[10]['ID'], '')
            self.assertEqual(rows[100]['ID'], '')
            self.assertEqual(rows[1000]['ID'], '')
            self.assertEqual(rows[1]['NUMBER'], '2147')
            self.assertEqual(rows[10]['NUMBER'], '605')
            self.assertEqual(rows[100]['NUMBER'], '167')
            self.assertEqual(rows[1000]['NUMBER'], '322')
            self.assertEqual(rows[1]['STREET'], 'BROADWAY')
            self.assertEqual(rows[10]['STREET'], 'HILLSBOROUGH ST')
            self.assertEqual(rows[100]['STREET'], '8TH ST')
            self.assertEqual(rows[1000]['STREET'], 'HANOVER AV')

    def test_single_sf(self):
        ''' Test complete process_one.process on San Francisco sample data.
        '''
        source = join(self.src_dir, 'us-ca-san_francisco.json')

        with HTTMock(self.response_content), \
             mock.patch('openaddr.preview.render') as preview_ren, \
             mock.patch('openaddr.slippymap.generate') as slippymap_gen:
            preview_ren.side_effect = touch_second_arg_file
            slippymap_gen.side_effect = touch_first_arg_file
            state_path = process_one.process(source, self.testdir, "addresses", "default", True, mapbox_key='mapbox-XXXX')

        self.assertTrue(slippymap_gen.mock_calls[0][1][0].endswith('.mbtiles'))
        self.assertTrue(slippymap_gen.mock_calls[0][1][1].endswith('.csv'))

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['cache'])
        self.assertIsNotNone(state['processed'])
        self.assertIsNotNone(state['sample'])
        self.assertIsNotNone(state['preview'])
        self.assertIsNotNone(state['slippymap'])
        self.assertEqual(state['geometry type'], 'Point')
        self.assertIsNone(state['website'])
        self.assertEqual(state['license'], '')

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertTrue('ZIPCODE' in sample_data[0])
        self.assertTrue('94102' in sample_data[1])

        output_path = join(dirname(state_path), state['processed'])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(rows[1]['ID'], '')
            self.assertEqual(rows[10]['ID'], '')
            self.assertEqual(rows[100]['ID'], '')
            self.assertEqual(rows[1000]['ID'], '')
            self.assertEqual(rows[1]['NUMBER'], '27')
            self.assertEqual(rows[10]['NUMBER'], '42')
            self.assertEqual(rows[100]['NUMBER'], '209')
            self.assertEqual(rows[1000]['NUMBER'], '1415')
            self.assertEqual(rows[1]['STREET'], 'OCTAVIA ST')
            self.assertEqual(rows[10]['STREET'], 'GOLDEN GATE AVE')
            self.assertEqual(rows[100]['STREET'], 'OCTAVIA ST')
            self.assertEqual(rows[1000]['STREET'], 'FOLSOM ST')
            self.assertEqual(rows[1]['UNIT'], '')
            self.assertEqual(rows[10]['UNIT'], '')
            self.assertEqual(rows[100]['UNIT'], '')
            self.assertEqual(rows[1000]['UNIT'], '')

    def test_single_car(self):
        ''' Test complete process_one.process on Carson sample data.
        '''
        source = join(self.src_dir, 'us-ca-carson.json')

        with HTTMock(self.response_content), \
             mock.patch('openaddr.preview.render') as preview_ren, \
             mock.patch('openaddr.slippymap.generate') as slippymap_gen:
            preview_ren.side_effect = touch_second_arg_file
            slippymap_gen.side_effect = touch_first_arg_file
            state_path = process_one.process(source, self.testdir, "addresses", "default", True, mapbox_key='mapbox-XXXX')

        self.assertTrue(slippymap_gen.mock_calls[0][1][0].endswith('.mbtiles'))
        self.assertTrue(slippymap_gen.mock_calls[0][1][1].endswith('.csv'))

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['cache'])
        self.assertEqual(state['fingerprint'], 'ab128c167aacd1cd970990b33872742e')
        self.assertIsNotNone(state['processed'])
        self.assertIsNotNone(state['sample'])
        self.assertIsNotNone(state['preview'])
        self.assertIsNotNone(state['slippymap'])
        self.assertEqual(state['geometry type'], 'Point')
        self.assertEqual(state['website'], 'http://ci.carson.ca.us/')
        self.assertIsNone(state['license'])

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertTrue('SITENUMBER' in sample_data[0])

        with open(join(dirname(state_path), state['processed'])) as file:
            rows = list(DictReader(file, dialect='excel'))
            self.assertEqual(5, len(rows))
            self.assertEqual(rows[0]['NUMBER'], '555')
            self.assertEqual(rows[0]['STREET'], 'CARSON ST')
            self.assertEqual(rows[0]['UNIT'], '')
            self.assertEqual(rows[0]['CITY'], 'CARSON, CA')
            self.assertEqual(rows[0]['POSTCODE'], '90745')
            self.assertEqual(rows[0]['DISTRICT'], '')
            self.assertEqual(rows[0]['REGION'], '')
            self.assertEqual(rows[0]['ID'], '')

    def test_single_car_cached(self):
        ''' Test complete process_one.process on Carson sample data.
        '''
        source = join(self.src_dir, 'us-ca-carson-cached.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['cache'])
        self.assertEqual(state['fingerprint'], 'aa01f23348547dd54a8f7b6af8f1ab49')
        self.assertIsNotNone(state['processed'])
        self.assertIsNotNone(state['sample'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])
        self.assertEqual(state['geometry type'], 'Point')

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertTrue('SITENUMBER' in sample_data[0])

        with open(join(dirname(state_path), state['processed'])) as file:
            self.assertTrue('555,CARSON ST' in file.read())

    def test_single_car_old_cached(self):
        ''' Test complete process_one.process on Carson sample data.
        '''
        source = join(self.src_dir, 'us-ca-carson-old-cached.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['cache'])
        self.assertEqual(state['fingerprint'], 'aa01f23348547dd54a8f7b6af8f1ab49')
        self.assertIsNotNone(state['processed'])
        self.assertIsNotNone(state['sample'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])
        self.assertEqual(state['geometry type'], 'Point')

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertTrue('SITENUMBER' in sample_data[0])

        with open(join(dirname(state_path), state['processed'])) as file:
            self.assertTrue('555,CARSON ST' in file.read())

    def test_single_tx_runnels(self):
        ''' Test complete process_one.process on Oakland sample data.
        '''
        source = join(self.src_dir, 'us/tx/runnels.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNone(state['cache'])
        self.assertIsNone(state['processed'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])

        # This test data does not contain a working conform object
        self.assertEqual(state['source problem'], "Missing required ESRI token")

    def test_single_oak(self):
        ''' Test complete process_one.process on Oakland sample data.
        '''
        source = join(self.src_dir, 'us-ca-oakland.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertFalse(state['skipped'])
        self.assertIsNotNone(state['cache'])
        # This test data does not contain a working conform object
        self.assertEqual(state['source problem'], "Unknown source conform format")
        self.assertIsNone(state["processed"])
        self.assertIsNone(state["preview"])
        self.assertIsNone(state["slippymap"])
        self.assertEqual(state["website"], 'http://data.openoakland.org/dataset/property-parcels/resource/df20b818-0d16-4da8-a9c1-a7b8b720ff49')
        self.assertIsNone(state["license"])

        with open(join(dirname(state_path), state["sample"])) as file:
            sample_data = json.load(file)

        self.assertTrue('FID_PARCEL' in sample_data[0])

    def test_single_oak_skip(self):
        ''' Test complete process_one.process on Oakland sample data.
        '''
        source = join(self.src_dir, 'us-ca-oakland-skip.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        # This test data says "skip": True
        self.assertEqual(state["source problem"], "Source says to skip")
        self.assertTrue(state["skipped"])
        self.assertIsNone(state["cache"])
        self.assertIsNone(state["processed"])
        self.assertIsNone(state["preview"])
        self.assertIsNone(state["slippymap"])

    def test_single_berk(self):
        ''' Test complete process_one.process on Berkeley sample data.
        '''
        source = join(self.src_dir, 'us-ca-berkeley.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state["cache"])
        # This test data does not contain a conform object at all
        self.assertEqual(state["source problem"], "Source is missing a conform object")
        self.assertIsNone(state["processed"])
        self.assertIsNone(state["preview"])
        self.assertIsNone(state["slippymap"])
        self.assertEqual(state["website"], 'http://www.ci.berkeley.ca.us/datacatalog/')
        self.assertIsNone(state["license"])

        with open(join(dirname(state_path), state["sample"])) as file:
            sample_data = json.load(file)

        self.assertTrue('APN' in sample_data[0])

    def test_single_berk_404(self):
        ''' Test complete process_one.process on 404 sample data.
        '''
        source = join(self.src_dir, 'us-ca-berkeley-404.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertEqual(state["source problem"], "Could not download source data")
        self.assertIsNone(state["cache"])
        self.assertIsNone(state["processed"])
        self.assertIsNone(state["preview"])
        self.assertIsNone(state["slippymap"])

    def test_single_berk_apn(self):
        ''' Test complete process_one.process on Berkeley sample data.
        '''
        source = join(self.src_dir, 'us-ca-berkeley-apn.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['cache'])
        self.assertIsNotNone(state['processed'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])
        self.assertEqual(state['website'], 'http://www.ci.berkeley.ca.us/datacatalog/')
        self.assertIsNone(state['license'])

        output_path = join(dirname(state_path), state['processed'])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(rows[1]['ID'], '055 188300600')
            self.assertEqual(rows[10]['ID'], '055 189504000')
            self.assertEqual(rows[100]['ID'], '055 188700100')
            self.assertEqual(rows[1]['NUMBER'], '2418')
            self.assertEqual(rows[10]['NUMBER'], '2029')
            self.assertEqual(rows[100]['NUMBER'], '2298')
            self.assertEqual(rows[1]['STREET'], 'DANA ST')
            self.assertEqual(rows[10]['STREET'], 'CHANNING WAY')
            self.assertEqual(rows[100]['STREET'], 'DURANT AVE')
            self.assertEqual(rows[1]['UNIT'], u'')
            self.assertEqual(rows[10]['UNIT'], u'')
            self.assertEqual(rows[100]['UNIT'], u'')

    def test_single_pl_ds(self):
        ''' Test complete process_one.process on Polish sample data.
        '''
        source = join(self.src_dir, 'pl-dolnoslaskie.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['cache'])
        self.assertIsNotNone(state['processed'])
        self.assertIsNotNone(state['sample'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])
        self.assertEqual(state['geometry type'], 'Point')
        self.assertIsNone(state['website'])
        self.assertEqual(state['license'][:21], 'Polish Law on Geodesy')
        self.assertEqual(state['share-alike'], 'false')
        self.assertIn('issues/187#issuecomment-63327973', state['license'])

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertTrue('pad_numer_porzadkowy' in sample_data[0])
        self.assertTrue(u'Wrocław' in sample_data[1])
        self.assertTrue(u'Ulica Księcia Witolda ' in sample_data[1])

    def test_single_pl_l(self):
        ''' Test complete process_one.process on Polish sample data.
        '''
        source = join(self.src_dir, 'pl-lodzkie.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['cache'])
        self.assertIsNotNone(state['processed'])
        self.assertIsNotNone(state['sample'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])
        self.assertEqual(state['geometry type'], 'Point')
        self.assertIsNone(state['website'])
        self.assertEqual(state['license'][:21], 'Polish Law on Geodesy')
        self.assertEqual(state['share-alike'], 'false')
        self.assertIn('issues/187#issuecomment-63327973', state['license'])

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertTrue('pad_numer_porzadkowy' in sample_data[0])
        self.assertTrue(u'Gliwice' in sample_data[1])
        self.assertTrue(u'Ulica Dworcowa ' in sample_data[1])

        output_path = join(dirname(state_path), state['processed'])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(rows[1]['NUMBER'], u'5')
            self.assertEqual(rows[10]['NUMBER'], u'8')
            self.assertEqual(rows[100]['NUMBER'], u'5a')
            self.assertEqual(rows[1]['STREET'], u'Ulica Dolnych Wa\u0142\xf3w  Gliwice')
            self.assertEqual(rows[10]['STREET'], u'Ulica Dolnych Wa\u0142\xf3w  Gliwice')
            self.assertEqual(rows[100]['STREET'], u'Plac pl. Inwalid\xf3w Wojennych  Gliwice')
            self.assertEqual(rows[1]['UNIT'], u'')
            self.assertEqual(rows[10]['UNIT'], u'')
            self.assertEqual(rows[100]['UNIT'], u'')

    def test_single_jp_fukushima2(self):
        ''' Test complete process_one.process on Japanese sample data.
        '''
        source = join(self.src_dir, 'jp-fukushima2.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state["sample"])
        self.assertIsNone(state["source problem"])
        self.assertIsNotNone(state["processed"])
        self.assertIsNone(state["preview"])
        self.assertIsNone(state["slippymap"])
        self.assertEqual(state["website"], 'http://nlftp.mlit.go.jp/isj/index.html')
        self.assertEqual(state["license"], u'http://nlftp.mlit.go.jp/ksj/other/yakkan.html')
        self.assertEqual(state["attribution required"], 'true')
        self.assertIn('Ministry of Land', state["attribution name"])

        with open(join(dirname(state_path), state["sample"])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertTrue(u'大字・町丁目名' in sample_data[0])
        self.assertTrue(u'田沢字姥懐' in sample_data[1])
        self.assertTrue('37.706391' in sample_data[1])
        self.assertTrue('140.480007' in sample_data[1])

        with open(join(dirname(state_path), state["processed"]), encoding='utf8') as file:
            rows = list(csv.DictReader(file))

        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]['NUMBER'], u'24-9')
        self.assertEqual(rows[0]['STREET'], u'田沢字姥懐')
        self.assertEqual(rows[1]['NUMBER'], u'16-9')
        self.assertEqual(rows[1]['STREET'], u'田沢字躑躅ケ森')
        self.assertEqual(rows[2]['NUMBER'], u'22-9')
        self.assertEqual(rows[2]['STREET'], u'小田字正夫田')
        self.assertEqual(rows[0]['GEOM'], 'POINT (140.480007 37.706391)')
        self.assertEqual(rows[1]['GEOM'], 'POINT (140.486267 37.707664)')
        self.assertEqual(rows[2]['GEOM'], 'POINT (140.41875 37.710239)')

    def test_single_utah(self):
        ''' Test complete process_one.process on data that uses file selection with mixed case (issue #104)
        '''
        source = join(self.src_dir, 'us-ut.json')

        with mock.patch('openaddr.util.request_ftp_file', new=self.response_content_ftp):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['sample'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])
        self.assertIsNone(state['website'])
        self.assertIsNone(state['license'])

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)

    def test_single_iceland(self):
        ''' Test complete process_one.process.
        '''
        source = join(self.src_dir, 'iceland.json')

        with mock.patch('openaddr.util.request_ftp_file', new=self.response_content_ftp):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])
        self.assertIsNotNone(state['processed'])
        self.assertIsNotNone(state['cache'])
        self.assertIsNotNone(state['sample'])
        self.assertIsNotNone(state['website'])
        self.assertIsNotNone(state['license'])

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        row1 = dict(zip(sample_data[0], sample_data[1]))
        row2 = dict(zip(sample_data[0], sample_data[2]))
        row3 = dict(zip(sample_data[0], sample_data[3]))
        row4 = dict(zip(sample_data[0], sample_data[4]))
        self.assertEqual(row1['HEITI_NF'], u'2.Gata v/Rauðavatn')
        self.assertEqual(row2['GAGNA_EIGN'], u'Þjóðskrá Íslands')
        self.assertEqual(row3['LONG_WGS84'], '-21,76846217953')
        self.assertEqual(row4['LAT_WGS84'], '64,110044369942')

        with open(join(dirname(state_path), state['processed']), encoding='utf8') as file:
            rows = list(csv.DictReader(file))

        self.assertEqual(len(rows), 15)
        self.assertEqual(rows[0]['STREET'], u'2.Gata v/Rauðavatn')
        self.assertEqual(rows[2]['GEOM'], 'POINT (-21.7684622 64.110974)')
        self.assertEqual(rows[3]['GEOM'], 'POINT (-21.7665982 64.1100444)')

    def test_single_fr_paris(self):
        ''' Test complete process_one.process on data that uses conform csvsplit (issue #124)
        '''
        source = join(self.src_dir, 'fr-paris.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['sample'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])
        self.assertEqual(state['website'], 'http://adresse.data.gouv.fr/download/')
        self.assertIsNone(state['license'])
        self.assertEqual(state['attribution required'], 'true')
        self.assertEqual(state['share-alike'], 'true')
        self.assertIn(u'Géographique et Forestière', state['attribution name'])

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertTrue('libelle_acheminement' in sample_data[0])
        self.assertTrue('Paris 15e Arrondissement' in sample_data[1])
        self.assertTrue('2.29603434925049' in sample_data[1])
        self.assertTrue('48.845110357374' in sample_data[1])

    def test_single_fr_lareunion(self):
        ''' Test complete process_one.process on data that uses non-UTF8 encoding (issue #136)
        '''
        source = None

        for form in ('NFC', 'NFD'):
            normalized = normalize(form, u'fr/la-réunion.json')
            if os.path.exists(join(self.src_dir, normalized)):
                source = join(self.src_dir, normalized)
                break

        if source is None:
            raise Exception('Could not find a usable fr/la-réunion.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['sample'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])
        self.assertEqual(state['website'], 'http://adresse.data.gouv.fr/download/')
        self.assertIsNone(state['license'])
        self.assertEqual(state['attribution required'], 'true')
        self.assertEqual(state['share-alike'], 'true')
        self.assertIn(u'Géographique et Forestière', state['attribution name'])

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertTrue('libelle_acheminement' in sample_data[0])
        self.assertTrue('Saint-Joseph' in sample_data[1])
        self.assertTrue('55.6120442584072' in sample_data[1])
        self.assertTrue('-21.385871079156' in sample_data[1])

    def test_single_va_statewide(self):
        ''' Test complete process_one.process on data with non-OGR .csv filename.
        '''
        source = join(self.src_dir, 'us/va/statewide.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['sample'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertTrue('ADDRNUM' in sample_data[0])
        self.assertTrue('393' in sample_data[1])
        self.assertTrue('36.596097285069824' in sample_data[1])
        self.assertTrue('-81.260533627271982' in sample_data[1])

    def test_single_oh_trumbull(self):
        ''' Test complete process_one.process on data with .txt filename present.
        '''
        source = join(self.src_dir, 'us/oh/trumbull.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['sample'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertTrue('HOUSENUM' in sample_data[0])
        self.assertTrue(775 in sample_data[1])
        self.assertTrue(2433902.038 in sample_data[1])
        self.assertTrue(575268.364 in sample_data[1])

    def test_single_ks_brown(self):
        ''' Test complete process_one.process on data with ESRI multiPolyline geometries.
        '''
        source = join(self.src_dir, 'us/ks/brown_county.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['sample'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertTrue('OA:GEOM' in sample_data[0])

    def test_single_pa_lancaster(self):
        ''' Test complete process_one.process on data with ESRI multiPolyline geometries.
        '''
        source = join(self.src_dir, 'us/pa/lancaster.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['sample'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)

        self.assertEqual(['ADRNUM', 'FDPRE', 'FDSUF', 'FNAME', 'FTYPE', 'MUNI', 'UNITNUM', 'OA:GEOM'], sample_data[0])
        self.assertEqual([
            '423',
            'W',
            ' ',
            '28TH DIVISION',
            'HWY',
            'ELIZABETH TOWNSHIP',
            '1',
            'POINT (-76.320967 40.2323465)'
        ], sample_data[1])

        output_path = join(dirname(state_path), state['processed'])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(rows[1]['UNIT'], u'2')
            self.assertEqual(rows[11]['UNIT'], u'11')
            self.assertEqual(rows[21]['UNIT'], u'')
            self.assertEqual(rows[1]['NUMBER'], u'423')
            self.assertEqual(rows[11]['NUMBER'], u'423')
            self.assertEqual(rows[21]['NUMBER'], u'7')
            self.assertEqual(rows[1]['STREET'], u'W 28TH DIVISION HWY')
            self.assertEqual(rows[11]['STREET'], u'W 28TH DIVISION HWY')
            self.assertEqual(rows[21]['STREET'], u'W 28TH DIVISION HWY')

    def test_single_ua_kharkiv(self):
        ''' Test complete process_one.process on data with ESRI multiPolyline geometries.
        '''
        source = join(self.src_dir, 'ua-63-city_of_kharkiv.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['sample'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 2)
        self.assertIn('OA:GEOM', sample_data[0])
        self.assertIn('FULLADDRU', sample_data[0])
        self.assertIn('SUFIXRU', sample_data[0])

    def test_single_pa_bucks(self):
        ''' Test complete process_one.process on data with ESRI multiPolyline geometries.
        '''
        source = join(self.src_dir, 'us/pa/bucks.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['sample'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)
            row1 = dict(zip(sample_data[0], sample_data[1]))
            row2 = dict(zip(sample_data[0], sample_data[2]))

        self.assertEqual(len(sample_data), 6)
        self.assertIn('SITUS_ADDR_NUM', sample_data[0])
        self.assertIn('MUNI', sample_data[0])
        self.assertEqual('', row1['SITUS_ADDR_NUM'])
        self.assertEqual('STATE', row1['SITUS_FNAME'])
        self.assertEqual('RD', row1['SITUS_FTYPE'])
        self.assertEqual('', row2['SITUS_ADDR_NUM'])
        self.assertEqual('STATE', row2['SITUS_FNAME'])
        self.assertEqual('RD', row2['SITUS_FTYPE'])

        output_path = join(dirname(state_path), state['processed'])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(rows[1]['UNIT'], u'')
            self.assertEqual(rows[10]['UNIT'], u'')
            self.assertEqual(rows[20]['UNIT'], u'')
            self.assertEqual(rows[1]['NUMBER'], u'')
            self.assertEqual(rows[10]['NUMBER'], u'')
            self.assertEqual(rows[20]['NUMBER'], u'429')
            self.assertEqual(rows[1]['STREET'], u'STATE RD')
            self.assertEqual(rows[10]['STREET'], u'STATE RD')
            self.assertEqual(rows[20]['STREET'], u'WALNUT AVE E')

    def test_single_nm_washington(self):
        ''' Test complete process_one.process on data without ESRI support for resultRecordCount.
        '''
        source = join(self.src_dir, 'us/nm/washington.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['sample'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertIn('OA:GEOM', sample_data[0])
        self.assertIn('BLDG_NUM', sample_data[0])
        self.assertEqual('7710', sample_data[1][0])
        self.assertEqual([' ', 'IVERSON', 'AVE', 'S'], sample_data[1][3:7])
        self.assertEqual('7710', sample_data[1][0])
        self.assertEqual('9884', sample_data[2][0])
        self.assertEqual('9030', sample_data[3][0])
        self.assertEqual('23110', sample_data[4][0])
        self.assertEqual(' ', sample_data[5][0])

        output_path = join(dirname(state_path), state['processed'])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(rows[1]['UNIT'], u'')
            self.assertEqual(rows[5]['UNIT'], u'')
            self.assertEqual(rows[9]['UNIT'], u'')
            self.assertEqual(rows[1]['NUMBER'], u'9884')
            self.assertEqual(rows[5]['NUMBER'], u'3842')
            self.assertEqual(rows[9]['NUMBER'], u'')
            self.assertEqual(rows[1]['STREET'], u'5TH STREET LN N')
            self.assertEqual(rows[5]['STREET'], u'ABERCROMBIE LN')
            self.assertEqual(rows[9]['STREET'], u'')

    def test_single_tx_waco(self):
        ''' Test complete process_one.process on data without ESRI support for resultRecordCount.
        '''
        source = join(self.src_dir, 'us/tx/city_of_waco.json')

        with HTTMock(self.response_content):
            ofs = csv.field_size_limit()
            csv.field_size_limit(1)
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)
            csv.field_size_limit(ofs)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNone(state["sample"], 'Sample should be missing when csv.field_size_limit() is too short')
        self.assertEqual(state["source problem"], "Could not conform source data")
        self.assertIsNone(state["processed"])

        source = join(self.src_dir, 'us/tx/city_of_waco.json')

        with HTTMock(self.response_content):
            ofs = csv.field_size_limit()
            csv.field_size_limit(sys.maxsize)
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)
            csv.field_size_limit(ofs)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state["sample"], 'Sample should be present when csv.field_size_limit() is long enough')
        self.assertIsNone(state["source problem"])
        self.assertIsNotNone(state["processed"])
        self.assertIsNone(state["preview"])
        self.assertIsNone(state["slippymap"])

        output_path = join(dirname(state_path), state["processed"])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(rows[0]['REGION'], u'TX')
            self.assertEqual(rows[0]['ID'], u'')
            self.assertEqual(rows[0]['NUMBER'], u'308')
            self.assertEqual(rows[0]['HASH'], u'e26a7cc0bdb9005c')
            self.assertEqual(rows[0]['CITY'], u'Mcgregor')
            self.assertEqual(rows[0]['GEOM'], u'POINT (-97.3961768 31.4432706)')
            self.assertEqual(rows[0]['STREET'], u'PULLEN ST')
            self.assertEqual(rows[0]['POSTCODE'], u'76657')
            self.assertEqual(rows[0]['UNIT'], u'')
            self.assertEqual(rows[0]['DISTRICT'], u'')

    def test_single_wy_park(self):
        ''' Test complete process_one.process on data without ESRI support for resultRecordCount.
        '''
        source = join(self.src_dir, 'us-wy-park.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state["sample"])
        self.assertIsNotNone(state["processed"])

        output_path = join(dirname(state_path), state["processed"])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(rows[0]['ID'], u'')
            self.assertEqual(rows[0]['NUMBER'], u'162')
            self.assertEqual(rows[0]['HASH'], u'fa774c4d6e199cb1')
            self.assertEqual(rows[0]['CITY'], u'')
            self.assertEqual(rows[0]['GEOM'], u'POINT (-108.7563613 44.7538737)')
            self.assertEqual(rows[0]['STREET'], u'N CLARK ST')
            self.assertEqual(rows[0]['POSTCODE'], u'')
            self.assertEqual(rows[0]['UNIT'], u'')
            self.assertEqual(rows[0]['DISTRICT'], u'')

    def test_single_ny_orange(self):
        ''' Test complete process_one.process on data NaN values in ESRI response.
        '''
        source = join(self.src_dir, 'us-ny-orange.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state["sample"])
        self.assertIsNotNone(state["processed"])

        output_path = join(dirname(state_path), state["processed"])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(rows[0]['ID'], u'')
            self.assertEqual(rows[0]['NUMBER'], u'434')
            self.assertEqual(rows[0]['HASH'], u'94540fc042f07760')
            self.assertEqual(rows[0]['CITY'], u'MONROE')
            self.assertEqual(rows[0]['GEOM'], u'POINT (-74.1926686 41.3187728)')
            self.assertEqual(rows[0]['STREET'], u'')
            self.assertEqual(rows[0]['POSTCODE'], u'10950')
            self.assertEqual(rows[0]['UNIT'], u'')
            self.assertEqual(rows[0]['DISTRICT'], u'')

    def test_single_de_berlin(self):
        ''' Test complete process_one.process on data.
        '''
        source = join(self.src_dir, 'de/berlin.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        output_path = join(dirname(state_path), state['processed'])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(rows[0]['NUMBER'], u'72')
            self.assertEqual(rows[1]['NUMBER'], u'3')
            self.assertEqual(rows[2]['NUMBER'], u'75')
            self.assertEqual(rows[0]['STREET'], u'Otto-Braun-Stra\xdfe')
            self.assertEqual(rows[1]['STREET'], u'Dorotheenstra\xdfe')
            self.assertEqual(rows[2]['STREET'], u'Alte Jakobstra\xdfe')

        self.assertIsNotNone(state['sample'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)

        for (sample_datum, row) in zip(sample_data[1:], rows[0:]):
            self.assertEqual(sample_datum[9], row['NUMBER'])
            self.assertEqual(sample_datum[13], row['STREET'])

    def test_single_us_or_portland(self):
        ''' Test complete process_one.process on data.
        '''
        source = join(self.src_dir, 'us/or/portland.json')

        with mock.patch('openaddr.util.request_ftp_file', new=self.response_content_ftp):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        output_path = join(dirname(state_path), state['processed'])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(len(rows), 12)
            self.assertEqual(rows[2]['NUMBER'], u'1')
            self.assertEqual(rows[3]['NUMBER'], u'10')
            self.assertEqual(rows[-2]['NUMBER'], u'2211')
            self.assertEqual(rows[-1]['NUMBER'], u'2211')
            self.assertEqual(rows[2]['STREET'], u'SW RICHARDSON ST')
            self.assertEqual(rows[3]['STREET'], u'SW PORTER ST')
            self.assertEqual(rows[-2]['STREET'], u'SE OCHOCO ST')
            self.assertEqual(rows[-1]['STREET'], u'SE OCHOCO ST')
            self.assertTrue(bool(rows[2]['GEOM']))
            self.assertTrue(bool(rows[3]['GEOM']))
            self.assertFalse(bool(rows[-2]['GEOM']))
            self.assertTrue(bool(rows[-1]['GEOM']))

    def test_single_nl_countrywide(self):
        ''' Test complete process_one.process on data.
        '''
        source = join(self.src_dir, 'nl/countrywide.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        output_path = join(dirname(state_path), state['processed'])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(len(rows), 8)
            self.assertEqual(rows[0]['NUMBER'], u'34x')
            self.assertEqual(rows[1]['NUMBER'], u'65-x')
            self.assertEqual(rows[2]['NUMBER'], u'147x-x')
            self.assertEqual(rows[3]['NUMBER'], u'6')
            self.assertEqual(rows[4]['NUMBER'], u'279b')
            self.assertEqual(rows[5]['NUMBER'], u'10')
            self.assertEqual(rows[6]['NUMBER'], u'601')
            self.assertEqual(rows[7]['NUMBER'], u'2')

    def test_single_be_wa_brussels(self):
        ''' Test complete process_one.process on data.
        '''
        source = join(self.src_dir, 'be/wa/brussels-fr.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        output_path = join(dirname(state_path), state['processed'])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(len(rows), 666)
            self.assertEqual(rows[0]['NUMBER'], u'2')
            self.assertEqual(rows[0]['STREET'], u'Rue de la Victoire')
            self.assertEqual(rows[1]['NUMBER'], u'16')
            self.assertEqual(rows[1]['STREET'], u'Rue Fontainas')
            self.assertEqual(rows[2]['NUMBER'], u'23C')
            self.assertEqual(rows[2]['STREET'], u'Rue Fontainas')
            self.assertEqual(rows[3]['NUMBER'], u'2')
            self.assertEqual(rows[3]['STREET'], u"Rue de l'Eglise Saint-Gilles")

            self.assertEqual(rows[0]['GEOM'], 'POINT (4.3458216 50.8324706)')
            self.assertEqual(rows[1]['GEOM'], 'POINT (4.3412631 50.8330868)')
            self.assertEqual(rows[2]['GEOM'], 'POINT (4.3410663 50.8334315)')
            self.assertEqual(rows[3]['GEOM'], 'POINT (4.3421632 50.8322201)')

    def test_single_it_52_statewide(self):
        ''' Test complete process_one.process on data.
        '''
        source = join(self.src_dir, 'it-52-statewide.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        output_path = join(dirname(state_path), state['processed'])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(len(rows), 19)
            self.assertEqual(rows[0]['NUMBER'], u'33')
            self.assertEqual(rows[0]['STREET'], u'VIA CARLO CARRÀ')
            self.assertEqual(rows[1]['NUMBER'], u'23')
            self.assertEqual(rows[1]['STREET'], u'VIA CARLO CARRÀ')
            self.assertEqual(rows[2]['NUMBER'], u'2')
            self.assertEqual(rows[2]['STREET'], u'VIA MARINO MARINI')
            self.assertEqual(rows[0]['GEOM'], 'POINT (10.1863188 43.9562646)')
            self.assertEqual(rows[1]['GEOM'], 'POINT (10.1856048 43.9558156)')
            self.assertEqual(rows[2]['GEOM'], 'POINT (10.1860548 43.9553626)')

    def test_single_us_nj_statewide(self):
        ''' Test complete process_one.process on data.
        '''
        source = join(self.src_dir, 'us/nj/statewide.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        output_path = join(dirname(state_path), state['processed'])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(len(rows), 1045)
            self.assertEqual(rows[0]['NUMBER'], u'7')
            self.assertEqual(rows[0]['STREET'], u'Sagamore Avenue')
            self.assertEqual(rows[1]['NUMBER'], u'29')
            self.assertEqual(rows[1]['STREET'], u'Sagamore Avenue')
            self.assertEqual(rows[2]['NUMBER'], u'47')
            self.assertEqual(rows[2]['STREET'], u'Seneca Place')
            self.assertEqual(rows[0]['GEOM'], 'POINT (-74.0012016 40.3201199)')
            self.assertEqual(rows[1]['GEOM'], 'POINT (-74.0027904 40.3203365)')
            self.assertEqual(rows[2]['GEOM'], 'POINT (-74.0011386 40.3166497)')

    def test_single_cz_countrywide(self):
        ''' Test complete process_one.process on data.
        '''
        source = join(self.src_dir, 'cz-countrywide-bad-tests.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIs(state["tests passed"], False)
        self.assertIsNone(state["sample"])
        self.assertIsNone(state["processed"])
        self.assertEqual(state["source problem"], "An acceptance test failed")

    def test_single_or_curry(self):
        ''' Test complete process_one.process on data.
        '''
        source = join(self.src_dir, 'us-or-curry.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertTrue(state["tests passed"])
        self.assertIsNone(state["sample"])
        self.assertIsNone(state["processed"])
        self.assertEqual(state["source problem"], "Could not download source data")

    def test_single_mi_grand_traverse(self):
        '''
        '''
        source = join(self.src_dir, 'us-mi-grand_traverse.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNone(state["processed"])
        self.assertEqual(state["source problem"], "Found no addresses in source data")

    def test_single_lake_man_gdb(self):
        ''' Test complete process_one.process on data.
        '''
        source = join(self.src_dir, 'lake-man-gdb.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['sample'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertTrue('ADDRESSID' in sample_data[0])
        self.assertTrue(964 in sample_data[1])
        self.assertTrue('FRUITED PLAINS LN' in sample_data[1])

        output_path = join(dirname(state_path), state['processed'])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(len(rows), 6)
            self.assertEqual(rows[0]['NUMBER'], '5115')
            self.assertEqual(rows[0]['STREET'], 'FRUITED PLAINS LN')
            self.assertEqual(rows[1]['NUMBER'], '5121')
            self.assertEqual(rows[1]['STREET'], 'FRUITED PLAINS LN')
            self.assertEqual(rows[2]['NUMBER'], '5133')
            self.assertEqual(rows[2]['STREET'], 'FRUITED PLAINS LN')
            self.assertEqual(rows[3]['NUMBER'], '5126')
            self.assertEqual(rows[3]['STREET'], 'FRUITED PLAINS LN')
            self.assertEqual(rows[4]['NUMBER'], '5120')
            self.assertEqual(rows[4]['STREET'], 'FRUITED PLAINS LN')
            self.assertEqual(rows[5]['NUMBER'], '5115')
            self.assertEqual(rows[5]['STREET'], 'OLD MILL RD')

    def test_single_lake_man_gdb_nested(self):
        ''' Test complete process_one.process on data.
        '''
        source = join(self.src_dir, 'lake-man-gdb-nested.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['sample'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertTrue('ADDRESSID' in sample_data[0])
        self.assertTrue(964 in sample_data[1])
        self.assertTrue('FRUITED PLAINS LN' in sample_data[1])

        output_path = join(dirname(state_path), state['processed'])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(len(rows), 6)
            self.assertEqual(rows[0]['NUMBER'], '5115')
            self.assertEqual(rows[0]['STREET'], 'FRUITED PLAINS LN')
            self.assertEqual(rows[1]['NUMBER'], '5121')
            self.assertEqual(rows[1]['STREET'], 'FRUITED PLAINS LN')
            self.assertEqual(rows[2]['NUMBER'], '5133')
            self.assertEqual(rows[2]['STREET'], 'FRUITED PLAINS LN')
            self.assertEqual(rows[3]['NUMBER'], '5126')
            self.assertEqual(rows[3]['STREET'], 'FRUITED PLAINS LN')
            self.assertEqual(rows[4]['NUMBER'], '5120')
            self.assertEqual(rows[4]['STREET'], 'FRUITED PLAINS LN')
            self.assertEqual(rows[5]['NUMBER'], '5115')
            self.assertEqual(rows[5]['STREET'], 'OLD MILL RD')

    def test_single_lake_man_gdb_nested_nodir(self):
        ''' Test complete process_one.process on data.
        '''
        source = join(self.src_dir, 'lake-man-gdb-nested-nodir.json')

        with HTTMock(self.response_content):
            state_path = process_one.process(source, self.testdir, "addresses", "default", False)

        with open(state_path) as file:
            state = dict(zip(*json.load(file)))

        self.assertIsNotNone(state['sample'])
        self.assertIsNone(state['preview'])
        self.assertIsNone(state['slippymap'])

        with open(join(dirname(state_path), state['sample'])) as file:
            sample_data = json.load(file)

        self.assertEqual(len(sample_data), 6)
        self.assertTrue('ADDRESSID' in sample_data[0])
        self.assertTrue(964 in sample_data[1])
        self.assertTrue('FRUITED PLAINS LN' in sample_data[1])

        output_path = join(dirname(state_path), state['processed'])

        with open(output_path, encoding='utf8') as input:
            rows = list(csv.DictReader(input))
            self.assertEqual(len(rows), 6)
            self.assertEqual(rows[0]['NUMBER'], '5115')
            self.assertEqual(rows[0]['STREET'], 'FRUITED PLAINS LN')
            self.assertEqual(rows[1]['NUMBER'], '5121')
            self.assertEqual(rows[1]['STREET'], 'FRUITED PLAINS LN')
            self.assertEqual(rows[2]['NUMBER'], '5133')
            self.assertEqual(rows[2]['STREET'], 'FRUITED PLAINS LN')
            self.assertEqual(rows[3]['NUMBER'], '5126')
            self.assertEqual(rows[3]['STREET'], 'FRUITED PLAINS LN')
            self.assertEqual(rows[4]['NUMBER'], '5120')
            self.assertEqual(rows[4]['STREET'], 'FRUITED PLAINS LN')
            self.assertEqual(rows[5]['NUMBER'], '5115')
            self.assertEqual(rows[5]['STREET'], 'OLD MILL RD')

class TestState (unittest.TestCase):

    def setUp(self):
        '''
        '''
        self.output_dir = tempfile.mkdtemp(prefix='TestState-')

    def tearDown(self):
        '''
        '''
        shutil.rmtree(self.output_dir)

    def test_write_state(self):
        '''
        '''
        log_handler = mock.Mock()

        with open(join(self.output_dir, 'log-handler-stream.txt'), 'w') as file:
            log_handler.stream.name = file.name

        with open(join(self.output_dir, 'processed.zip'), 'w') as file:
            processed_path = file.name

        with open(join(self.output_dir, 'preview.png'), 'w') as file:
            preview_path = file.name

        with open(join(self.output_dir, 'slippymap.mbtiles'), 'w') as file:
            slippymap_path = file.name

        conform_result = ConformResult(processed=None, sample='/tmp/sample.json',
                                       website='http://example.com', license='ODbL',
                                       geometry_type='Point', address_count=999,
                                       path=processed_path, elapsed=timedelta(seconds=1),
                                       attribution_flag=True, attribution_name='Example',
                                       sharealike_flag=True)

        cache_result = CacheResult(cache='http://example.com/cache.csv',
                                   fingerprint='ff9900', version='0.0.0',
                                   elapsed=timedelta(seconds=2))

        #
        # Check result of process_one.write_state().
        #
        args = dict(source='sources/foo.json', layer='addresses',
                    data_source_name='open-data', skipped=False,
                    destination=self.output_dir, log_handler=log_handler,
                    cache_result=cache_result, conform_result=conform_result,
                    temp_dir=self.output_dir, preview_path=preview_path,
                    slippymap_path=slippymap_path, tests_passed=True)

        path1 = process_one.write_state(**args)

        with open(path1) as file:
            state1 = dict(zip(*json.load(file)))

        self.assertEqual(state1['source'], 'foo.json')
        self.assertEqual(state1['skipped'], False)
        self.assertEqual(state1['cache'], 'http://example.com/cache.csv')
        self.assertEqual(state1['sample'], 'sample.json')
        self.assertEqual(state1['website'], 'http://example.com')
        self.assertEqual(state1['license'], 'ODbL')
        self.assertEqual(state1['geometry type'], 'Point')
        self.assertEqual(state1['address count'], 999)
        self.assertEqual(state1['version'], '0.0.0')
        self.assertEqual(state1['fingerprint'], 'ff9900')
        self.assertEqual(state1['cache time'], '0:00:02')
        self.assertEqual(state1['processed'], 'out.zip')
        self.assertEqual(state1['process time'], '0:00:01')
        self.assertEqual(state1['output'], 'output.txt')
        self.assertEqual(state1['preview'], 'preview.png')
        self.assertEqual(state1['slippymap'], 'slippymap.mbtiles')
        self.assertEqual(state1['share-alike'], 'true')
        self.assertEqual(state1['attribution required'], 'true')
        self.assertEqual(state1['attribution name'], 'Example')
        self.assertEqual(state1['tests passed'], True)

        #
        # Tweak a few values, try process_one.write_state() again.
        #
        conform_result.attribution_flag = False

        args.update(source='sources/foo/bar.json', skipped=True)
        path2 = process_one.write_state(**args)

        with open(path2) as file:
            state2 = dict(zip(*json.load(file)))

        self.assertEqual(state2['source'], 'bar.json')
        self.assertEqual(state2['skipped'], True)
        self.assertEqual(state2['attribution required'], 'false')

    def test_find_source_problem(self):
        '''
        '''
        self.assertIsNone({'source problem': find_source_problem('', {'coverage': {'US Census': None}})}["source problem"])
        self.assertIsNone({'source problem': find_source_problem('', {'coverage': {'US Census': None}})}["source problem"])
        self.assertIsNone({'source problem': find_source_problem('', {'coverage': {'ISO 3166': None}})}["source problem"])

        self.assertIs({'source problem': find_source_problem('', {})}["source problem"], SourceProblem.no_coverage)
        self.assertIs({'source problem': find_source_problem('WARNING: Could not download ESRI source data: Could not retrieve layer metadata: Token Required', {})}["source problem"], SourceProblem.no_esri_token)
        self.assertIs({'source problem': find_source_problem('WARNING: Error doing conform; skipping', {})}["source problem"], SourceProblem.conform_source_failed)
        self.assertIs({'source problem': find_source_problem('WARNING: Could not download source data', {})}["source problem"], SourceProblem.download_source_failed)
        self.assertIs({'source problem': find_source_problem('WARNING: Unknown source conform protocol', {})}["source problem"], SourceProblem.unknown_conform_protocol)
        self.assertIs({'source problem': find_source_problem('WARNING: Unknown source conform format', {})}["source problem"], SourceProblem.unknown_conform_format)
        self.assertIs({'source problem': find_source_problem('WARNING: Unknown source conform type', {})}["source problem"], SourceProblem.unknown_conform_type)
        self.assertIs({'source problem': find_source_problem('WARNING: A source test failed', {})}["source problem"], SourceProblem.test_failed)
        self.assertIs({'source problem': find_source_problem('WARNING: Found no addresses in source data', {})}["source problem"], SourceProblem.no_addresses_found)

class TestPackage (unittest.TestCase):

    def test_package_output_csv(self):
        '''
        '''
        processed_csv = '/tmp/stuff.csv'
        website, license = 'http://ci.carson.ca.us/', 'Public domain'

        with mock.patch('zipfile.ZipFile') as ZipFile:
            package_output('us-ca-carson', processed_csv, website, license)

            self.assertEqual(len(ZipFile.return_value.mock_calls), 4)
            call1, call2, call3, call4 = ZipFile.return_value.mock_calls

        self.assertEqual(call1[0], 'writestr')
        self.assertEqual(call1[1][0], 'README.txt')
        readme_text = call1[1][1].decode('utf8')
        self.assertTrue(website in readme_text)
        self.assertTrue(license in readme_text)

        self.assertEqual(call2[0], 'writestr')
        self.assertEqual(call2[1][0], 'us-ca-carson.vrt')
        vrt_content = call2[1][1].decode('utf8')
        self.assertTrue('<OGRVRTLayer name="us-ca-carson">' in vrt_content)
        self.assertTrue('<SrcDataSource relativeToVRT="1">' in vrt_content)
        self.assertTrue('us-ca-carson.csv' in vrt_content)

        self.assertEqual(call3[0], 'write')
        self.assertEqual(call3[1][0], processed_csv)
        self.assertEqual(call3[1][1], 'us-ca-carson.csv')

        self.assertEqual(call4[0], 'close')

    def test_package_output_txt(self):
        '''
        '''
        processed_txt = '/tmp/stuff.txt'
        website, license = 'http://ci.carson.ca.us/', 'Public domain'

        with mock.patch('zipfile.ZipFile') as ZipFile:
            package_output('us-ca-carson', processed_txt, website, license)

            self.assertEqual(len(ZipFile.return_value.mock_calls), 3)
            call1, call2, call3 = ZipFile.return_value.mock_calls

        self.assertEqual(call1[0], 'writestr')
        self.assertEqual(call1[1][0], 'README.txt')
        readme_text = call1[1][1].decode('utf8')
        self.assertTrue(website in readme_text)
        self.assertTrue(license in readme_text)

        self.assertEqual(call2[0], 'write')
        self.assertEqual(call2[1][0], processed_txt)
        self.assertEqual(call2[1][1], 'us-ca-carson.txt')

        self.assertEqual(call3[0], 'close')

@contextmanager
def locked_open(filename):
    ''' Open and lock a file, for use with threads and processes.
    '''
    with open(filename, 'r+b') as file:
        if lockf:
            lockf(file, LOCK_EX)
        yield file
        if lockf:
            lockf(file, LOCK_UN)

class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code
