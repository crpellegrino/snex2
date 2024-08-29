from django.test import TestCase
from django.contrib.auth.models import Group
from django.db.utils import IntegrityError
from tom_observations.models import ObservationRecord, ObservationGroup, DynamicCadence
from tom_targets.models import BaseTarget
from custom_code.views import cancel_observation, CustomTargetCreateView
from custom_code.forms import CustomTargetCreateForm
from datetime import datetime, timedelta
from unittest.mock import patch
from django.http import HttpRequest
import json
import logging

logger = logging.getLogger(__name__)

# Create your tests here.
class TestScheduling(TestCase):

    def setUp(self):
        target = BaseTarget.objects.create(
            ra=123.456, 
            dec=0.00, 
            type='SIDEREAL',
            name='Test Target'
            )
        
        obs = ObservationRecord.objects.create(
            target=target,
            facility='LCO',
            parameters={
                'ipp_value': 1.0,
                'max_airmass': 1.6,
                'cadence_strategy': 'TestCadenceStrategy',
                'cadence_frequency': 3.0,
                'facility': 'LCO',
                'reminder': datetime.strftime(datetime.now() + timedelta(days=3), '%Y-%m-%dT%H:%M:%S'),
                'name': 'TestTarget',
                'target_id': target.id,
                'start': datetime.strftime(datetime.now(), '%Y-%m-%dT%H:%M:%S'),
                'end': datetime.strftime(datetime.now() + timedelta(days=3), '%Y-%m-%dT%H:%M:%S'),
                'observation_type': 'IMAGING',
                'B': [300.0, 2, 1]
            },
            observation_id='test',
            status='COMPLETED'
        )

        obsgroup = ObservationGroup.objects.create(
            name='Test group',
        )
        obsgroup.observation_records.add(obs)

        DynamicCadence.objects.create(
            observation_group_id=obsgroup.id,
            cadence_strategy='TestCadenceStrategy',
            cadence_parameters={
                'cadence_frequency': 3.0
            },
            active=True
        )

    def test_modify(self):
        assert True

    def test_continue(self):
        assert True

    def test_cancel(self):
        obs = ObservationRecord.objects.get(observation_id='test')
        self.assertTrue(cancel_observation(obs))
        self.assertFalse(obs.observationgroup_set.first().dynamiccadence_set.first().active)


class TestTargetCreation(TestCase):

    def setUp(self):
        group = Group.objects.create(name='Test group')
        self.form_parameters = {
            'groups': [group],
            'sciencetags': [],
            'ra': 123.456,
            'dec': 0.00,
            'name': 'test_target',
            'type': 'SIDEREAL'
        }

    @patch('custom_code.views.CustomTargetCreateView.run_hook_with_snex1')
    def test_form_valid(self, mock_run_hook_with_snex1):
        mock_run_hook_with_snex1.return_value = True

        ### Test target creation works
        form = CustomTargetCreateForm(self.form_parameters, instance=None)
        cv = CustomTargetCreateView()
        cv.form_valid(form)
        self.assertTrue(BaseTarget.objects.filter(name=self.form_parameters['name']).exists())

        ### Test target creation fails with duplicate name
        BaseTarget.objects.filter(name=self.form_parameters['name']).delete()
        BaseTarget.objects.create(name=self.form_parameters['name'])
        cv = CustomTargetCreateView()
        cv.request = HttpRequest()
        cv.form_valid(form)
        self.assertTrue(BaseTarget.objects.filter(name=self.form_parameters['name']).count() == 1)

        ### Test target creation fails with duplicate coordinates
        BaseTarget.objects.filter(name=self.form_parameters['name']).delete()
        BaseTarget.objects.create(
            ra=self.form_parameters['ra'], 
            dec=self.form_parameters['dec'], 
            name='another_test_target', 
            type='SIDEREAL'
        )
        cv = CustomTargetCreateView()
        cv.request = HttpRequest()
        cv.form_valid(form)
        self.assertTrue(BaseTarget.objects.filter(
            ra=self.form_parameters['ra'],
            dec=self.form_parameters['dec']
        ).count() == 1)

