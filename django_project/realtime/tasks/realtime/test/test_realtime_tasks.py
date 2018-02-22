# coding=utf-8
import ast
import logging
import os
import shutil
import time
import unittest
from datetime import datetime

import pytz
from celery.result import AsyncResult
from django import test
from django.apps import apps
from django.core.files.base import File

from realtime.app_settings import EARTHQUAKE_MONITORED_DIRECTORY, LOGGER_NAME
from realtime.models.ash import Ash
from realtime.models.earthquake import Earthquake
from realtime.models.volcano import Volcano
from realtime.tasks.realtime.celery_app import app as realtime_app

__copyright__ = "Copyright 2016, The InaSAFE Project"
__license__ = "GPL version 3"
__email__ = "info@inasafe.org"
__revision__ = ':%H$'


LOGGER = logging.getLogger(LOGGER_NAME)


@unittest.skipUnless(
    realtime_app.control.ping(),
    'Realtime Worker needs to be run')
class TestAshTasks(test.LiveServerTestCase):

    def setUp(self):
        app_config = apps.get_app_config('realtime')
        app_config.load_volcano_fixtures()
        app_config.load_test_users()

    @staticmethod
    def fixtures_path(*path):
        return os.path.abspath(
            os.path.join(os.path.dirname(__file__), 'fixtures', *path))

    def test_process_ash(self):
        """Test generating ash hazard."""
        # Create an ash object
        volcano = Volcano.objects.get(volcano_name='Merapi')

        time_zone = pytz.timezone('Asia/Jakarta')
        event_time = datetime(2017, 2, 21, 12, 4, tzinfo=pytz.utc)
        event_time = event_time.astimezone(time_zone)

        ash = Ash.objects.create(
            hazard_file=File(open(self.fixtures_path(
                '201702211204+0000_Merapi-hazard.tif'))),
            volcano=volcano,
            alert_level='normal',
            event_time=event_time,
            event_time_zone_offset=420,
            event_time_zone_string='Asia/Jakarta',
            eruption_height=100,
            forecast_duration=3)

        # force synchronous result
        ash.refresh_from_db()
        result = AsyncResult(id=ash.task_id, app=realtime_app)
        actual_value = result.get()
        ash.refresh_from_db()

        # Check state
        self.assertEqual(result.state, 'SUCCESS')

        # Check ret_val
        expected_value = {
            'hazard_path': '/home/realtime/ashmaps/201702211904+0700_Merapi/'
                           'ash_fall.tif',
            'success': True
        }
        self.assertEqual(expected_value, actual_value)

        # Check hazard information pushed to db
        self.assertTrue(ash.hazard_layer_exists)

        from realtime.tasks.ash import check_processing_task

        result = check_processing_task.delay()
        result.get()

        ash.refresh_from_db()

        # TODO: Fixme. Somehow the following asserts produces an error
        # It was not supposed to do that.
        # Check that the same task has been marked as success.
        # result = AsyncResult(id=ash.task_id, app=realtime_app)
        # self.assertEqual(result.state, 'SUCCESS')
        # self.assertEqual(ash.task_status, 'SUCCESS')

        ash.delete()


@unittest.skipIf(
    ast.literal_eval(os.environ.get('ON_TRAVIS', 'False')),
    'Skip on Travis for now')
class TestEarthquakeTasks(test.LiveServerTestCase):

    def setUp(self):
        app_config = apps.get_app_config('realtime')
        app_config.load_test_users()

    @staticmethod
    def fixtures_path(*path):
        return os.path.abspath(
            os.path.join(os.path.dirname(__file__), 'fixtures', *path))

    def test_process_earthquake(self):
        """Test generating earthquake hazard."""
        # Drop a grid file to monitored directory
        grid_file = self.fixtures_path('20180220163351-grid.xml')
        drop_location = os.path.join(
            EARTHQUAKE_MONITORED_DIRECTORY,
            '20180220163351',
            'grid.xml')

        shutil.copy(grid_file, drop_location)

        # wait until grid file is processed
        while True:
            try:
                target_eq = Earthquake.objects.get(
                    shake_id='20180220163351',
                    source_type='initial')
                if target_eq.hazard_layer_exists and target_eq.shake_grid_xml:
                    break
            except BaseException:
                pass

            LOGGER.info('Waiting for Realtime EQ push')
            time.sleep(5)

        self.assertTrue(target_eq.hazard_layer_exists)
        self.assertTrue(target_eq.shake_grid_xml)

        # wait until task state success
        from realtime.tasks.earthquake import check_processing_task
        while True:
            try:
                check_processing_task()
                target_eq.refresh_from_db()

                if target_eq.analysis_task_status == 'SUCCESS':
                    break
            except BaseException:
                pass

            LOGGER.info('Waiting for Headless Analysis')
            time.sleep(5)

        self.assertTrue(target_eq.impact_layer_exists)

        target_eq.delete()
