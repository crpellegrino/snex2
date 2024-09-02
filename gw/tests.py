from django.test import TestCase
from django.template.loader import render_to_string
from django.urls import reverse
from django.contrib.auth.models import User, Group
from datetime import datetime
from tom_nonlocalizedevents.models import NonLocalizedEvent, EventSequence, EventLocalization
from gw.models import GWFollowupGalaxy
from gw.templatetags.gw_tags import galaxy_distribution


class TestGWFollowupGalaxyListView(TestCase):

    def setUp(self):

        nle = NonLocalizedEvent.objects.create(
            state='ACTIVE', 
            event_type='GW', 
            event_id='MockEvent'
        )
        loc = EventLocalization.objects.create(
            nonlocalizedevent=nle,
            date=datetime.now()
            )
        self.seq = EventSequence.objects.create(nonlocalizedevent=nle, localization=loc)
        self.gal = GWFollowupGalaxy.objects.create(
            ra=123.456, 
            dec=0.0, 
            eventlocalization=loc,
            catalog_objname='Test Galaxy',
            score=0.5
        )
        self.user = User.objects.create(username='test_user')
        group = Group.objects.create(name='GWO4')
        self.user.groups.add(group)
        self.user.save()

        kwargs = {'id': self.seq.id}
        self.url = reverse(
            'nonlocalizedevents-galaxies', kwargs=kwargs
        )

    def test_view_page_loads(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.gal.catalog_objname)

