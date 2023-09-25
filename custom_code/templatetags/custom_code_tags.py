from plotly import offline
import plotly.graph_objs as go
from django import template, forms
from django.conf import settings
from django.db.models.functions import Lower
from django.shortcuts import reverse
from guardian.shortcuts import get_objects_for_user, get_groups_with_perms
from django.contrib.auth.models import User, Group
from django_comments.models import Comment
from django.contrib.contenttypes.models import ContentType

from tom_targets.models import Target, TargetExtra, TargetList
from tom_targets.forms import TargetVisibilityForm
from tom_observations import utils, facility
from tom_dataproducts.models import DataProduct, ReducedDatum
from tom_dataproducts.forms import DataShareForm
from tom_observations.models import ObservationRecord, ObservationGroup, DynamicCadence
from tom_common.hooks import run_hook

from astroplan import Observer, FixedTarget, AtNightConstraint, time_grid_from_range, moon_illumination
import datetime
import json
from astropy.time import Time
from astropy import units as u
from astropy.coordinates import get_moon, get_sun, SkyCoord, AltAz
import numpy as np
import time
import matplotlib.pyplot as plt

from custom_code.models import *
from custom_code.forms import CustomDataProductUploadForm, PapersForm, PhotSchedulingForm, SpecSchedulingForm, ReferenceStatusForm, ThumbnailForm
from urllib.parse import urlencode
from tom_observations.utils import get_sidereal_visibility
from custom_code.facilities.lco_facility import SnexPhotometricSequenceForm, SnexSpectroscopicSequenceForm
from custom_code.thumbnails import make_thumb
import base64
import logging

logger = logging.getLogger(__name__)

register = template.Library()

@register.inclusion_tag('custom_code/airmass_collapse.html')
def airmass_collapse(target):
    interval = 30 #min
    airmass_limit = 3.0

    obj = Target
    obj.ra = target.ra
    obj.dec = target.dec
    obj.epoch = 2000
    obj.type = 'SIDEREAL' 

    plot_data = get_24hr_airmass(obj, interval, airmass_limit)
    layout = go.Layout(
        xaxis=dict(gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        yaxis=dict(range=[airmass_limit,1.0],gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        margin=dict(l=20,r=10,b=30,t=40),
        hovermode='closest',
        width=250,
        height=200,
        showlegend=False,
        plot_bgcolor='white'
    )
    visibility_graph = offline.plot(
            go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False, config={'staticPlot': True}, include_plotlyjs='cdn'
    )
    return {
        'target': target,
        'figure': visibility_graph
    }

@register.inclusion_tag('custom_code/airmass.html', takes_context=True)
def airmass_plot(context):
    #request = context['request']
    interval = 15 #min
    airmass_limit = 3.0
    plot_data = get_24hr_airmass(context['object'], interval, airmass_limit)

    ### Get the amount of time each site is above airmass 1.6 and 2.0
    for t in plot_data:
        time_vals = t['x']
        airmass_vals = np.asarray(t['y'])
        vals_above_airmass_low = np.where(airmass_vals < 1.6)
        vals_above_airmass_high = np.where(airmass_vals < 2.0)
        
        ### Have to do this in a complex way to avoid nonsense answers when the
        ### visibility plots wrap around to 24 hours from now
        if len(vals_above_airmass_low[0]) > 0:
            time_diffs = np.asarray(time_vals[vals_above_airmass_low]) - min(time_vals[vals_above_airmass_low])
            valid_time_diffs = np.where(time_diffs < datetime.timedelta(hours=12))
            ### Get the ones that are ~24 hours from now too
            tomorrow_time_diffs = np.where(time_diffs > datetime.timedelta(hours=12))

            if len(tomorrow_time_diffs[0]) > 0: # Visibility plot wrapped, so account for that
                time_diff = max(time_diffs[valid_time_diffs]) + (max(time_diffs[tomorrow_time_diffs]) - min(time_diffs[tomorrow_time_diffs]))
            
            else:
                time_diff = max(time_diffs[valid_time_diffs])
            time_above_airmass_low = round(time_diff.total_seconds() / 3600, 1)
        
        else:
            time_above_airmass_low = 0.0

        if len(vals_above_airmass_high[0]) > 0:
            time_diffs = np.asarray(time_vals[vals_above_airmass_high]) - min(time_vals[vals_above_airmass_high])
            valid_time_diffs = np.where(time_diffs < datetime.timedelta(hours=12))
            tomorrow_time_diffs = np.where(time_diffs > datetime.timedelta(hours=12))
            
            if len(tomorrow_time_diffs[0]) > 0:
                time_diff = max(time_diffs[valid_time_diffs]) + (max(time_diffs[tomorrow_time_diffs]) - min(time_diffs[tomorrow_time_diffs]))
            
            else:
                time_diff = max(time_diffs[valid_time_diffs])
            time_above_airmass_high = round(time_diff.total_seconds() / 3600, 1)
        
        else:
            time_above_airmass_high = 0.0
        
        text = 'Time Above Airmass 1.6: {} hr;Time Above Airmass 2.0: {} hr'.format(time_above_airmass_low, time_above_airmass_high) 
        t['hovertemplate'] = '(%{customdata|%Y-%m-%d %H:%M:%S}, %{y:.2f})' + '<br>{}'.format(text.split(';')[0]) + '<br>{}'.format(text.split(';')[1])
        t['customdata'] = time_vals
        t['x'] = np.asarray([(time_val - datetime.datetime.utcnow()).total_seconds() / 3600 for time_val in time_vals])

    layout = go.Layout(
        xaxis=dict(gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True,title_text="Hours From Now"),
        yaxis=dict(range=[airmass_limit,1.0],gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        margin=dict(l=20,r=10,b=30,t=40),
        hovermode='closest',
        width=600,
        height=300,
        plot_bgcolor='white'
    )
    visibility_graph = offline.plot(
        go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False
    )
    return {
        'target': context['object'],
        'figure': visibility_graph
    }

def get_24hr_airmass(target, interval, airmass_limit):

    plot_data = []
    
    start = Time(datetime.datetime.utcnow())
    end = Time(start.datetime + datetime.timedelta(days=1))
    time_range = time_grid_from_range(
        time_range = [start, end],
        time_resolution = interval*u.minute)
    time_plot = time_range.datetime
    
    fixed_target = FixedTarget(name = target.name, 
        coord = SkyCoord(
            target.ra,
            target.dec,
            unit = 'deg'
        )
    )

    #Hack to speed calculation up by factor of ~3
    sun_coords = get_sun(time_range[int(len(time_range)/2)])
    fixed_sun = FixedTarget(name = 'sun',
        coord = SkyCoord(
            sun_coords.ra,
            sun_coords.dec,
            unit = 'deg'
        )
    )

    #Colors to match SNEx1
    colors = {
        'Siding Spring': '#3366cc',
        'Sutherland': '#dc3912',
        'Teide': '#8c6239',
        'Cerro Tololo': '#ff9900',
        'McDonald': '#109618',
        'Haleakala': '#990099'
    }

    for observing_facility in facility.get_service_classes():

        if observing_facility != 'LCO':
            continue

        observing_facility_class = facility.get_service_class(observing_facility)
        sites = observing_facility_class().get_observing_sites()

        for site, site_details in sites.items():

            observer = Observer(
                longitude = site_details.get('longitude')*u.deg,
                latitude = site_details.get('latitude')*u.deg,
                elevation = site_details.get('elevation')*u.m
            )
            
            sun_alt = observer.altaz(time_range, fixed_sun).alt
            obj_airmass = observer.altaz(time_range, fixed_target).secz

            bad_indices = np.argwhere(
                (obj_airmass >= airmass_limit) |
                (obj_airmass <= 1) |
                (sun_alt > -12*u.deg)  #between astro twilights
            )

            obj_airmass = [np.nan if i in bad_indices else float(x)
                for i, x in enumerate(obj_airmass)]

            label = '({facility}) {site}'.format(
                facility = observing_facility, site = site
            )

            plot_data.append(
                go.Scatter(x=time_plot, y=obj_airmass, mode='lines', name=label, marker=dict(color=colors[site]))
            )

    return plot_data


def get_color(filter_name, filter_translate):
    colors = {'U': 'rgb(59,0,113)',
        'B': 'rgb(0,87,255)',
        'V': 'rgb(120,255,0)',
        'g': 'rgb(0,204,255)',
        'r': 'rgb(255,124,0)',
        'i': 'rgb(144,0,43)',
        'g_ZTF': 'rgb(0,204,255)',
        'r_ZTF': 'rgb(255,124,0)',
        'i_ZTF': 'rgb(144,0,43)',
        'UVW2': '#FE0683',
        'UVM2': '#BF01BC',
        'UVW1': '#8B06FF',
        'cyan': 'rgb(0,128,128)',
        'orange': 'rgb(250,128,114)',
        'other': 'rgb(0,0,0)'}
    try: color = colors[filter_translate[filter_name]]
    except: color = colors['other']
    return color


def generic_lightcurve_plot(target, user):
    """
    Writing a generic function to return the data to plot
    for the different light curve applications SNEx2 uses
    """
    
    filter_translate = {'U': 'U', 'B': 'B', 'V': 'V',
        'g': 'g', 'gp': 'g', 'r': 'r', 'rp': 'r', 'i': 'i', 'ip': 'i',
        'g_ZTF': 'g_ZTF', 'r_ZTF': 'r_ZTF', 'i_ZTF': 'i_ZTF', 'UVW2': 'UVW2', 'UVM2': 'UVM2', 
        'UVW1': 'UVW1'}
    photometry_data = {}

    if settings.TARGET_PERMISSIONS_ONLY:
        datums = ReducedDatum.objects.filter(target=target, data_type=settings.DATA_PRODUCT_TYPES['photometry'][0])
    
    else:
        datums = get_objects_for_user(user,
                                      'tom_dataproducts.view_reduceddatum',
                                      klass=ReducedDatum.objects.filter(
                                        target=target,
                                        data_type=settings.DATA_PRODUCT_TYPES['photometry'][0]))
    for rd in datums:
    #for rd in ReducedDatum.objects.filter(target=target, data_type='photometry'):
        value = rd.value
        if not value:  # empty
            continue
        if isinstance(value, str):
            value = json.loads(value)

        filt = filter_translate.get(value.get('filter', ''), '')
   
        photometry_data.setdefault(filt, {})
        photometry_data[filt].setdefault('time', []).append(rd.timestamp)
        photometry_data[filt].setdefault('magnitude', []).append(value.get('magnitude',None))
        photometry_data[filt].setdefault('error', []).append(value.get('error', None))

    plot_data = [
        go.Scatter(
            x=filter_values['time'],
            y=filter_values['magnitude'], mode='markers',
            marker=dict(color=get_color(filter_name, filter_translate)),
            name=filter_translate.get(filter_name, ''),
            error_y=dict(
                type='data',
                array=filter_values['error'],
                visible=True,
                color=get_color(filter_name, filter_translate)
            )
        ) for filter_name, filter_values in photometry_data.items()]

    return plot_data


@register.inclusion_tag('custom_code/lightcurve.html', takes_context=True)
def lightcurve(context, target):
    
    plot_data = generic_lightcurve_plot(target, context['request'].user)         

    layout = go.Layout(
        xaxis=dict(gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        yaxis=dict(autorange='reversed',gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        margin=dict(l=30, r=10, b=100, t=40),
        hovermode='closest',
        plot_bgcolor='white'
        #height=500,
        #width=500
    )
    if plot_data:
      return {
          'target': target,
          'plot': offline.plot(go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False)
      }
    else:
        return {
            'target': target,
            'plot': 'No photometry for this target yet.'
        }


@register.inclusion_tag('custom_code/lightcurve_collapse.html')
def lightcurve_collapse(target, user):
    
    plot_data = generic_lightcurve_plot(target, user)     
    spec = ReducedDatum.objects.filter(target=target, data_type='spectroscopy')
    
    layout = go.Layout(
        xaxis=dict(gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        yaxis=dict(autorange='reversed',gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        margin=dict(l=30, r=10, b=30, t=40),
        hovermode='closest',
        height=200,
        width=250,
        showlegend=False,
        plot_bgcolor='white',
        shapes=[
            dict(
                type='line',
                yref='paper',
                y0=0,
                y1=1,
                xref='x',
                x0=s.timestamp,
                x1=s.timestamp,
                opacity=0.2,
                line=dict(color='black', dash='dash'),
            ) for s in spec]
    )
    if plot_data:
        return {
            'target': target,
            'plot': offline.plot(go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False, config={'staticPlot': True}, include_plotlyjs='cdn')
        }
    else:
        return {
            'target': target,
            'plot': 'No photometry for this target yet.'
        }

@register.inclusion_tag('custom_code/moon.html')
def moon_vis(target):

    day_range = 30
    times = Time(
        [str(datetime.datetime.utcnow() + datetime.timedelta(days=delta))
            for delta in np.arange(0, day_range, 0.2)],
        format = 'iso', scale = 'utc'
    )
    
    obj_pos = SkyCoord(target.ra, target.dec, unit=u.deg)
    moon_pos = get_moon(times)

    separations = moon_pos.separation(obj_pos).deg
    phases = moon_illumination(times)

    distance_color = 'rgb(0, 0, 255)'
    phase_color = 'rgb(255, 0, 0)'
    plot_data = [
        go.Scatter(x=times.mjd-times[0].mjd, y=separations, 
            mode='lines',name='Moon distance (degrees)',
            line=dict(color=distance_color)
        ),
        go.Scatter(x=times.mjd-times[0].mjd, y=phases, 
            mode='lines', name='Moon phase', yaxis='y2',
            line=dict(color=phase_color))
    ]
    layout = go.Layout(
        xaxis=dict(gridcolor='#D3D3D3', showline=True, linecolor='#D3D3D3', mirror=True, title='Days from now'),
        yaxis=dict(range=[0.,180.],tick0=0.,dtick=45.,
            tickfont=dict(color=distance_color),
            gridcolor='#D3D3D3', showline=True, linecolor='#D3D3D3', mirror=True
        ),
        yaxis2=dict(range=[0., 1.], tick0=0., dtick=0.25, overlaying='y', side='right',
            tickfont=dict(color=phase_color),
            gridcolor='#D3D3D3', showline=True, linecolor='#D3D3D3', mirror=True),
        margin=dict(l=20,r=10,b=30,t=40),
        width=600,
        height=300,
        plot_bgcolor='white'
    )
    figure = offline.plot(
        go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False
    )
   
    return {'plot': figure}


def bin_spectra(waves, fluxes, b):
    """
    Bins spectra given list of wavelengths, fluxes, and binning factor
    """
    binned_waves = []
    binned_flux = []
    newindex = 0
    for index in range(0, len(fluxes), b):
        if index + b - 1 <= len(fluxes) - 1:
            sumx = 0
            sumy = 0
            for binindex in range(index, index+b, 1):
                if binindex < len(fluxes):
                    sumx += waves[binindex]
                    sumy += fluxes[binindex]

            sumx = sumx / b
            sumy = sumy / b
        if sumx > 0:
            binned_waves.append(sumx)
            binned_flux.append(sumy)

    return binned_waves, binned_flux


@register.inclusion_tag('custom_code/spectra.html')
def spectra_plot(target, dataproduct=None):
    spectra = []
    spectral_dataproducts = ReducedDatum.objects.filter(target=target, data_type='spectroscopy').order_by('timestamp')
    if dataproduct:
        spectral_dataproducts = DataProduct.objects.get(dataproduct=dataproduct)
    
    colormap = plt.cm.gist_rainbow
    colors = [colormap(i) for i in np.linspace(0.99, 0, len(spectral_dataproducts))]
    rgb_colors = ['rgb({r}, {g}, {b})'.format(
        r=int(color[0]*255),
        g=int(color[1]*255),
        b=int(color[2]*255),
    ) for color in colors]

    for spectrum in spectral_dataproducts:
        datum = spectrum.value
        wavelength = []
        flux = []
        name = str(spectrum.timestamp).split(' ')[0]
        if datum.get('photon_flux'):
            wavelength = datum.get('wavelength')
            flux = datum.get('photon_flux')
        elif datum.get('flux'):
            wavelength = datum.get('wavelength')
            flux = datum.get('flux')
        else:
            for key, value in datum.items():
                wavelength.append(float(value['wavelength']))
                flux.append(float(value['flux']))

        binned_wavelength, binned_flux = bin_spectra(wavelength, flux, 5)
        spectra.append((binned_wavelength, binned_flux, name))
    plot_data = [
        go.Scatter(
            x=spectrum[0],
            y=spectrum[1],
            name=spectrum[2],
            line_color=rgb_colors[i]
        ) for i, spectrum in enumerate(spectra)]
    layout = go.Layout(
        height=450,
        width=600,
        hovermode='closest',
        xaxis=dict(
            tickformat="d",
            title='Wavelength (angstroms)',
            gridcolor='#D3D3D3',
            showline=True,
            linecolor='#D3D3D3',
            mirror=True
        ),
        yaxis=dict(
            tickformat=".1g",
            title='Flux',
            gridcolor='#D3D3D3',
            showline=True,
            linecolor='#D3D3D3',
            mirror=True
        ),
        plot_bgcolor='white',
        margin=dict(l=30, r=10, b=30, t=40),
    )
    if plot_data:
      return {
          'target': target,
          'plot': offline.plot(go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False)
      }
    else:
        return {
            'target': target,
            'plot': 'No spectra for this target yet.'
        }

@register.inclusion_tag('custom_code/spectra_collapse.html')
def spectra_collapse(target):
    spectra = []
    spectral_dataproducts = ReducedDatum.objects.filter(target=target, data_type='spectroscopy').order_by('-timestamp')
    for spectrum in spectral_dataproducts:
        datum = spectrum.value
        wavelength = []
        flux = []
        if datum.get('photon_flux'):
            wavelength = datum.get('wavelength')
            flux = datum.get('photon_flux')
        elif datum.get('flux'):
            wavelength = datum.get('wavelength')
            flux = datum.get('flux')
        else:
            for key, value in datum.items():
                wavelength.append(float(value['wavelength']))
                flux.append(float(value['flux']))
        
        binned_wavelength, binned_flux = bin_spectra(wavelength, flux, 5)
        spectra.append((binned_wavelength, binned_flux))
    plot_data = [
        go.Scatter(
            x=spectrum[0],
            y=spectrum[1]
        ) for spectrum in spectra]
    layout = go.Layout(
        height=200,
        width=250,
        margin=dict(l=30, r=10, b=30, t=40),
        showlegend=False,
        xaxis=dict(
            gridcolor='#D3D3D3',
            showline=True,
            linecolor='#D3D3D3',
            mirror=True
        ),
        yaxis=dict(
            showticklabels=False,
            gridcolor='#D3D3D3',
            showline=True,
            linecolor='#D3D3D3',
            mirror=True
        ),
        plot_bgcolor='white'
    )
    if plot_data:
      return {
          'target': target,
          'plot': offline.plot(go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False, config={'staticPlot': True}, include_plotlyjs='cdn')
      }
    else:
        return {
            'target': target,
            'plot': 'No spectra for this target yet.'
        }

@register.inclusion_tag('custom_code/aladin_collapse.html')
def aladin_collapse(target):
    return {'target': target}

@register.filter
def get_targetextra_id(target, keyword):
    try:
        targetextra = TargetExtra.objects.get(target_id=target.id, key=keyword)
        return targetextra.id
    except:
        return json.dumps(None)


@register.inclusion_tag('tom_targets/partials/target_data.html', takes_context=True)
def target_data_with_user(context, target):
    """
    Displays the data of a target.
    """
    user = context['request'].user
    extras = {k['name']: target.extra_fields.get(k['name'], '') for k in settings.EXTRA_FIELDS if not k.get('hidden')}
    return {
        'target': target,
        'extras': extras,
        'user': user
    }


@register.inclusion_tag('custom_code/classifications_dropdown.html')
def classifications_dropdown(target):
    classifications = [i for i in settings.TARGET_CLASSIFICATIONS]
    target_classification = TargetExtra.objects.filter(target=target, key='classification').first()
    if target_classification is None:
        target_class = None
    else:
        target_class = target_classification.value
    return {'target': target,
            'classifications': classifications,
            'target_class': target_class}

@register.inclusion_tag('custom_code/science_tags_dropdown.html')
def science_tags_dropdown(target):
    tag_query = ScienceTags.objects.all().order_by(Lower('tag'))
    tags = [i.tag for i in tag_query]
    return{'target': target,
           'sciencetags': tags}

@register.filter
def get_target_tags(target):
    #try:
    target_tag_query = TargetTags.objects.filter(target_id=target.id)
    tags = ''
    for i in target_tag_query:
        tag_name = ScienceTags.objects.filter(id=i.tag_id).first().tag
        tags+=(str(tag_name) + ',')
    return json.dumps(tags)
    #except:
    #    return json.dumps(None)


@register.inclusion_tag('custom_code/custom_upload_dataproduct.html', takes_context=True)
def custom_upload_dataproduct(context, obj):
    user = context['user']
    initial = {}
    choices = {}
    if isinstance(obj, Target):
        initial['target'] = obj
        initial['referrer'] = reverse('tom_targets:detail', args=(obj.id,))
        initial['used_in'] = ('', '')

    elif isinstance(obj, ObservationRecord):
        initial['observation_record'] = obj
        initial['referrer'] = reverse('tom_observations:detail', args=(obj.id,))
        
    form = CustomDataProductUploadForm(initial=initial)
    if not settings.TARGET_PERMISSIONS_ONLY:
        if user.is_superuser:
            form.fields['groups'].queryset = Group.objects.all()
        else:
            form.fields['groups'].queryset = user.groups.all()
    return {'data_product_form': form}


@register.inclusion_tag('custom_code/submit_lco_observations.html')
def submit_lco_observations(target):
    phot_initial = {'target_id': target.id,
                    'facility': 'LCO',
                    'observation_type': 'IMAGING',
                    'name': get_best_name(target)}
    spec_initial = {'target_id': target.id,
                    'facility': 'LCO',
                    'observation_type': 'SPECTRA',
                    'name': get_best_name(target)}
    phot_form = SnexPhotometricSequenceForm(initial=phot_initial, auto_id='phot_%s')
    spec_form = SnexSpectroscopicSequenceForm(initial=spec_initial, auto_id='spec_%s')
    phot_form.helper.form_action = reverse('submit-lco-obs', kwargs={'facility': 'LCO'})
    spec_form.helper.form_action = reverse('submit-lco-obs', kwargs={'facility': 'LCO'})
    if not settings.TARGET_PERMISSIONS_ONLY:
        phot_form.fields['groups'].queryset = Group.objects.all()
        spec_form.fields['groups'].queryset = Group.objects.all()
    return {'object': target,
            'phot_form': phot_form,
            'spec_form': spec_form}

@register.inclusion_tag('custom_code/dash_lightcurve.html', takes_context=True)
def dash_lightcurve(context, target, width, height):
    request = context['request']
    
    # Get initial choices and values for some dash elements
    telescopes = ['LCO']
    reducer_groups = []
    papers_used_in = []
    final_reduction = False
    background_subtracted = False

    datumquery = ReducedDatum.objects.filter(target=target, data_type='photometry')
    for i in datumquery:
        datum_value = i.value
        if isinstance(datum_value, str):
            datum_value = json.loads(datum_value)
        if datum_value.get('background_subtracted', '') == True:
            background_subtracted = True
            break

    final_background_subtracted = False
    for de in ReducedDatumExtra.objects.filter(target=target, key='upload_extras', data_type='photometry'):
        de_value = json.loads(de.value)
        inst = de_value.get('instrument', '')
        used_in = de_value.get('used_in', '')
        group = de_value.get('reducer_group', '')

        if inst and inst not in telescopes:
            telescopes.append(inst)
        if used_in and used_in not in papers_used_in:
            try:
                paper_query = Papers.objects.get(id=used_in)
                paper_string = str(paper_query)
                papers_used_in.append(paper_string)
            except:
                paper_string = str(used_in)
                papers_used_in.append(paper_string)
        if group and group not in reducer_groups:
            reducer_groups.append(group)
   
        if de_value.get('final_reduction', '')==True:
            final_reduction = True
            final_reduction_datumid = de_value.get('data_product_id', '')

            datum = ReducedDatum.objects.filter(target=target, data_type='photometry', data_product_id=final_reduction_datumid)
            datum_value = datum.first().value
            if isinstance(datum_value, str):
                datum_value = json.loads(datum_value)
            if datum_value.get('background_subtracted', '') == True:
                final_background_subtracted = True
    
    reducer_group_options = [{'label': 'LCO', 'value': ''}]
    reducer_group_options.extend([{'label': k, 'value': k} for k in reducer_groups])
    reducer_groups.append('')
    
    paper_options = [{'label': '', 'value': ''}]
    paper_options.extend([{'label': k, 'value': k} for k in papers_used_in])

    dash_context = {'target_id': {'value': target.id},
                    'plot-width': {'value': width},
                    'plot-height': {'value': height},
                    'telescopes-checklist': {'options': [{'label': k, 'value': k} for k in telescopes]},
                    'reducer-group-checklist': {'options': reducer_group_options,
                                                'value': reducer_groups},
                    'papers-dropdown': {'options': paper_options}
    }

    if final_reduction:
        dash_context['final-reduction-checklist'] = {'value': 'Final'}
        dash_context['reduction-type-radio'] = {'value': 'manual'}

        if final_background_subtracted:
            dash_context['subtracted-radio'] = {'value': 'Subtracted'}
        else:
            dash_context['subtracted-radio'] = {'value': 'Unsubtracted'}
            dash_context['telescopes-checklist']['value'] = telescopes

    elif background_subtracted:
        dash_context['subtracted-radio'] = {'value': 'Subtracted'}

    else:
        dash_context['subtracted-radio'] = {'value': 'Unsubtracted'}


    return {'dash_context': dash_context,
            'request': request}


@register.inclusion_tag('custom_code/dash_spectra.html', takes_context=True)
def dash_spectra(context, target):
    request = context['request']

    try:
        z = TargetExtra.objects.filter(target_id=target.id, key='redshift').first().float_value
    except:
        z = 0

    ### Send the min and max flux values 
    target_id = target.id
    spectral_dataproducts = ReducedDatum.objects.filter(target_id=target_id, data_type='spectroscopy')
    if not spectral_dataproducts:
        return {'dash_context': {},
                'request': request
            }
    colormap = plt.cm.gist_rainbow
    colors = [colormap(i) for i in np.linspace(0, 0.99, len(spectral_dataproducts))]
    rgb_colors = ['rgb({r}, {g}, {b})'.format(
        r=int(color[0]*255),
        g=int(color[1]*255),
        b=int(color[2]*255),
    ) for color in colors]
    all_data = []
    max_flux = 0
    min_flux = 0
    for i in range(len(spectral_dataproducts)):
        spectrum = spectral_dataproducts[i]
        datum = spectrum.value
        wavelength = []
        flux = []
        name = str(spectrum.timestamp).split(' ')[0]
        if datum.get('photon_flux'):
            wavelength = datum.get('wavelength')
            flux = datum.get('photon_flux')
        elif datum.get('flux'):
            wavelength = datum.get('wavelength')
            flux = datum.get('flux')
        else:
            for key, value in datum.items():
                wavelength.append(value['wavelength'])
                flux.append(float(value['flux']))
        if max(flux) > max_flux: max_flux = max(flux)
        if min(flux) < min_flux: min_flux = min(flux)

    dash_context = {'target_id': {'value': target.id},
                    'target_redshift': {'value': z},
                    'min-flux': {'value': min_flux},
                    'max-flux': {'value': max_flux}
                }

    return {'dash_context': dash_context,
            'request': request}

@register.inclusion_tag('custom_code/dataproduct_update.html')
def dataproduct_update(dataproduct):
    group_query = Group.objects.all()
    groups = [i.name for i in group_query]
    return{'dataproduct': dataproduct,
           'groups': groups}

@register.filter
def get_dataproduct_groups(dataproduct):
    # Query all the groups with permission for this dataproduct
    groups = ','.join([g.name for g in get_groups_with_perms(dataproduct)])
    return json.dumps(groups)


@register.inclusion_tag('tom_observations/partials/observation_plan.html')
def custom_observation_plan(target, facility, length=1, interval=30, airmass_limit=3.0):
    """
    Displays form and renders plot for visibility calculation. Using this templatetag to render a plot requires that
    the context of the parent view have values for start_time, end_time, and airmass.
    """

    visibility_graph = ''
    start_time = datetime.datetime.now()
    end_time = start_time + datetime.timedelta(days=length)

    visibility_data = get_sidereal_visibility(target, start_time, end_time, interval, airmass_limit)
    i = 0
    plot_data = []
    for site, data in visibility_data.items():
        plot_data.append(go.Scatter(x=data[0], y=data[1], mode='markers+lines', marker={'symbol': i}, name=site))
        i += 1
    layout = go.Layout(
        xaxis=dict(gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True,title='Date'),
        yaxis=dict(range=[airmass_limit,1.0],gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True,title='Airmass'),
        #xaxis={'title': 'Date'},
        #yaxis={'autorange': 'reversed', 'title': 'Airmass'},
        plot_bgcolor='white'
    )
    visibility_graph = offline.plot(
        go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False
    )

    return {
        'visibility_graph': visibility_graph
    }


@register.inclusion_tag('custom_code/observation_summary.html', takes_context=True)
def observation_summary(context, target=None, time='previous'):
    """
    A modification of the observation_list templatetag 
    to display a summary of the observation records
    for this object.
    """
    if target:
        if settings.TARGET_PERMISSIONS_ONLY:
            observations = target.observationrecord_set.all()
        else:
            observations = get_objects_for_user(
                                context['request'].user,
                                'tom_observations.view_observationrecord',
                                ).filter(target=target)
    else:
        observations = ObservationRecord.objects.all()

    observations = observations.order_by('parameters__start')

    if time == 'ongoing':
        cadences = DynamicCadence.objects.filter(active=True, observation_group__in=ObservationGroup.objects.filter(name__in=[o.parameters.get('name', '') for o in observations]))
    
    else:
        if time == 'pending':
            observations = observations.filter(observation_id='template pending')
        cadences = DynamicCadence.objects.filter(active=False, observation_group__in=ObservationGroup.objects.filter(name__in=[o.parameters.get('name', '') for o in observations]))
    
    parameters = []
    for cadence in cadences:
        obsgroup = ObservationGroup.objects.get(id=cadence.observation_group_id)
        #Check if the request is pending, and if so skip it
        pending_obs = obsgroup.observation_records.all().filter(observation_id='template pending').first()
        if not pending_obs and time == 'pending':
            continue
        
        if time == 'pending':
            observation = pending_obs
        else:
            observation = obsgroup.observation_records.all().filter(observation_id='template').first()
        if not observation:
            observation = obsgroup.observation_records.all().order_by('-id').first()
            first_observation = obsgroup.observation_records.all().order_by('id').first()
            sequence_start = str(first_observation.parameters.get('start')).split('T')[0]
            requested_str = ''
        else:
            sequence_start = str(observation.parameters.get('sequence_start', '')).split('T')[0]
            if not sequence_start:
                sequence_start = str(observation.parameters.get('start', '')).split('T')[0]
            requested_str = ', requested by {}'.format(str(observation.parameters.get('start_user', '')))

        parameter = observation.parameters

        # First do LCO observations
        if parameter.get('facility', '') == 'LCO':

            if 'SUPA202' in parameter.get('proposal', ''):
                title_suffix = ' [ePESSTO Proprietary]'
            elif 'LCO2022A' in parameter.get('proposal', ''):
                title_suffix = ' [DLT40 Proprietary]'
            else:
                title_suffix = ''

            if parameter.get('cadence_strategy', '') == 'SnexResumeCadenceAfterFailureStrategy' and float(parameter.get('cadence_frequency', 0.0)) > 0.0:
                parameter_string = str(parameter.get('cadence_frequency', '')) + '-day ' + str(parameter.get('observation_type', '')).lower() + ' cadence of '
            else:
                parameter_string = 'Single ' + str(parameter.get('observation_type', '')).lower() + ' observation of '

            if parameter.get('observation_type', '') == 'IMAGING':
                filters = ['U', 'B', 'V', 'R', 'I', 'u', 'gp', 'rp', 'ip', 'zs', 'w']
                for f in filters:
                    filter_parameters = parameter.get(f, '')
                    if filter_parameters:
                        if filter_parameters[0] != 0.0:
                            filter_string = f + ' (' + str(filter_parameters[0]) + 'x' + str(filter_parameters[1]) + '), '
                            parameter_string += filter_string 
            
            elif parameter.get('observation_type', '') == 'SPECTRA':
                parameter_string += str(parameter.get('exposure_time', ''))
                parameter_string += 's '

            if parameter.get('observation_mode') == 'TIME_CRITICAL':
                parameter_string += '(time critical) '
            elif parameter.get('observation_mode') == 'RAPID_RESPONSE':
                parameter_string += '(rapid response) '

            instrument_dict = {'2M0-FLOYDS-SCICAM': 'Floyds',
                               '1M0-SCICAM-SINISTRO': 'Sinistro',
                               '2M0-SCICAM-MUSCAT': 'Muscat',
                               '2M0-SPECTRAL-AG': 'Spectra',
                               '0M4-SCICAM-SBIG': 'SBIG'
            }

            if parameter.get('instrument_type') in instrument_dict.keys():
                parameter_string += 'with ' + instrument_dict[parameter.get('instrument_type')]

            parameter_string += ', IPP ' + str(parameter.get('ipp_value', ''))
            parameter_string += ' and airmass < ' + str(parameter.get('max_airmass', ''))
            parameter_string += ' starting on ' + sequence_start #str(parameter.get('start')).split('T')[0]
            endtime = parameter.get('sequence_end', '')
            if not endtime:
                endtime = parameter.get('end', '')

            if time == 'previous' and endtime:
                parameter_string += ' and ending on ' + str(endtime).split('T')[0]
            parameter_string += requested_str

            ### Get any comments associated with this observation group
            content_type_id = ContentType.objects.get(model='observationgroup').id
            comments = Comment.objects.filter(object_pk=obsgroup.id, content_type_id=content_type_id).order_by('id')
            comment_list = ['{}: {}'.format(User.objects.get(username=comment.user_name).first_name, comment.comment) for comment in comments]

            parameters.append({'title': 'LCO Sequence'+title_suffix,
                               'summary': parameter_string,
                               'comments': comment_list,
                               'observation': observation.id,
                               'group': obsgroup.id})

        # Now do Gemini observations
        elif parameter.get('facility', '') == 'Gemini':
            
            if 'SPECTRA' in parameter.get('observation_type', ''):
                parameter_string = 'Gemini spectrum of B exposure time ' + str(parameter.get('b_exptime', '')) + 's and R exposure time ' + str(parameter.get('r_exptime', '')) + 's with airmass <' + str(parameter.get('max_airmass', '')) + ', scheduled on ' + str(observation.created).split(' ')[0]

            else: # Gemini photometry
                parameter_string = 'Gemini photometry of g (' + str(parameter.get('g_exptime', '')) + 's), r (' + str(parameter.get('r_exptime', '')) + 's), i (' + str(parameter.get('i_exptime', '')) + 's), and z (' + str(parameter.get('z_exptime', '')) + 's), with airmass < ' + str(parameter.get('max_airmass', '')) + ', scheduled on ' + str(observation.created).split(' ')[0]

            parameters.append({'title': 'Gemini Sequence',
                               'summary': parameter_string,
                               'comments': [''], #No comment functionality for Gemini yet
                               'observation': observation.id,
                               'group': obsgroup.id})

    return {
        'observations': observations,
        'parameters': parameters,
        'time': time
    }


@register.inclusion_tag('custom_code/papers_list.html')
def papers_list(target):

    paper_query = Papers.objects.filter(target=target)
    papers = []
    for i in range(len(paper_query)):
        papers.append(paper_query[i])

    paper_form = PapersForm(initial={'target': target})
    
    return {'object': target,
            'papers': papers,
            'form': paper_form}


@register.inclusion_tag('custom_code/papers_form.html')
def papers_form(target):

    paper_form = PapersForm(initial={'target': target})
    return {'object': target,
            'form': paper_form}


@register.filter
def smart_name_list(target):

    namelist = [target.name] + [alias.name for alias in target.aliases.all()]
    good_names = []
    for name in namelist:
        if ('SN ' in name or 'AT ' in name or 'ZTF' in name) and name not in good_names:
            good_names.append(name)
        elif 'sn ' in name[:4] or 'at ' in name[:4] or 'ztf' in name[:4]:
            new_name = name.replace(name[:3], name[:3].upper())
            if new_name not in good_names:
                good_names.append(new_name)
        elif ('sn' in name[:2] or 'at' in name[:2] or 'SN' in name[:2] or 'AT' in name[:2]) and name not in good_names and ('las' not in name[:5] and 'LAS' not in name):
            new_name = name[:2].upper() + ' ' + name[2:]
            if new_name not in good_names:
                good_names.append(new_name)
        elif ('atlas' in name[:5] or 'ATLAS' in name[:5]):
            new_name = name.replace(name[:5], name[:5].upper())
            if new_name not in good_names:
                good_names.append(new_name)
        elif 'dlt' in name[:4]:
            new_name = name.replace(name[:3], name[:3].upper())
            if new_name not in good_names:
                good_names.append(new_name)
        elif name not in good_names:
            good_names.append(name)
    
    return good_names
    

def get_scheduling_form(observation, user_id, start, requested_str, case='notpending'):
    '''
    Used to get the initial parameters and form for scheduling current
    and pending sequences.
    '''
    parameters = []
    facility = observation.facility 
    obsgroup = observation.observationgroup_set.first()
    target = observation.target
    target_names = smart_name_list(observation.target)

    content_type_id = ContentType.objects.get(model='observationgroup').id
    comment = Comment.objects.filter(object_pk=obsgroup.id, content_type_id=content_type_id).order_by('id').first()
    if not comment:
        comment_str = ''
    else:
        comment_str = '{}: {}'.format(User.objects.get(username=comment.user_name).first_name, comment.comment)
    
    parameter = observation.parameters
    if parameter.get('observation_type', '') == 'IMAGING':

        observation_type = 'Phot'
        if '2M' in parameter.get('instrument_type', ''):
            instrument = 'Muscat'
        elif '1M' in parameter.get('instrument_type', ''):
            instrument = 'Sinistro'
        else:
            instrument = 'SBIG'

        cadence_frequency = parameter.get('cadence_frequency', '')
        #start = str(obsset.first().parameters['start']).replace('T', ' ')
        end = str(parameter.get('reminder', '')).replace('T', ' ')
        if not end:
            end = str(observation.modified).split('.')[0]

        if parameter.get('cadence_strategy', '') == 'SnexResumeCadenceAfterFailureStrategy':
            cadence_strat = '(Repeating)'
        else:
            cadence_strat = '(Onetime)'

        observing_parameters = {
                   'instrument_type': parameter.get('instrument_type', ''),
                   'min_lunar_distance': parameter.get('min_lunar_distance', ''),
                   'proposal': parameter.get('proposal', ''),
                   'observation_type': parameter.get('observation_type', ''),
                   'observation_mode': parameter.get('observation_mode', ''),
                   'cadence_strategy': parameter.get('cadence_strategy', ''),
                   'cadence_frequency': cadence_frequency
            }

        if instrument == 'Muscat':
            observing_parameters['guider_mode'] = parameter.get('guider_mode', '')
            observing_parameters['exposure_mode'] = parameter.get('exposure_mode', '')
            for pos in ['diffuser_g_position', 'diffuser_r_position', 'diffuser_i_position', 'diffuser_z_position']:
                observing_parameters[pos] = parameter.get(pos, '')

        initial = {'name': target.name,
                   'observation_id': observation.id,
                   'target_id': target.id,
                   'facility': facility,
                   'observation_type': parameter.get('observation_type', ''),
                   'cadence_strategy': parameter.get('cadence_strategy', ''),
                   'observing_parameters': json.dumps(observing_parameters),
                   'cadence_frequency': cadence_frequency,
                   'ipp_value': parameter.get('ipp_value', ''),
                   'max_airmass': parameter.get('max_airmass', ''),
                   'reminder': 2*cadence_frequency
            }
        
        filters = ['U', 'B', 'V', 'R', 'I', 'u', 'gp', 'rp', 'ip', 'zs', 'w']
        for f in filters:
            if parameter.get(f, '') and parameter.get(f, '')[0] != 0.0:
                initial[f] = parameter.get(f, '')

        form = PhotSchedulingForm(initial=initial)

        parameters.append({'observation_id': observation.id,
                           'obsgroup_id': obsgroup.id,
                           'target': target,
                           'names': target_names,
                           'facility': facility,
                           'proposal': parameter.get('proposal', ''),
                           'observation_type': observation_type,
                           'cadence_strategy': cadence_strat,
                           'instrument': instrument,
                           'start': start + ' by ' + requested_str,
                           'comment': comment_str,
                           'reminder': end,
                           'user_id': user_id,
                           'case': case
                        })
    
    else: # For spectra observations
        observation_type = 'Spec'
        instrument = 'Floyds'
        cadence_frequency = parameter.get('cadence_frequency', '')
        if parameter.get('cadence_strategy', '') == 'SnexResumeCadenceAfterFailureStrategy':
            cadence_strat = '(Repeating)'
        else:
            cadence_strat = '(Onetime)'
        #start = str(obsset.first().parameters['start']).replace('T', ' ')
        end = str(parameter.get('reminder', '')).replace('T', ' ')
        if not end:
            end = str(observation.modified).split('.')[0]

        observing_parameters = {
                   'instrument_type': parameter.get('instrument_type', ''),
                   'min_lunar_distance': parameter.get('min_lunar_distance', ''),
                   'proposal': parameter.get('proposal', ''),
                   'observation_type': parameter.get('observation_type', ''),
                   'observation_mode': parameter.get('observation_mode', ''),
                   'cadence_strategy': parameter.get('cadence_strategy', ''),
                   'cadence_frequency': cadence_frequency,
                   'site': parameter.get('site', ''),
                   'exposure_count': parameter.get('exposure_count', ''),
                   'acquisition_radius': parameter.get('acquisition_radius', ''),
                   'guider_mode': parameter.get('guider_mode', ''),
                   'guider_exposure_time': parameter.get('guider_exposure_time', ''),
                   'filter': parameter.get('filter', '')
            }

        initial = {'name': target.name,
                   'observation_id': observation.id,
                   'target_id': target.id,
                   'facility': facility,
                   'observation_type': parameter.get('observation_type', ''),
                   'cadence_strategy': parameter.get('cadence_strategy', ''),
                   'observing_parameters': json.dumps(observing_parameters),
                   'cadence_frequency': cadence_frequency,
                   'ipp_value': parameter.get('ipp_value', ''),
                   'max_airmass': parameter.get('max_airmass', ''),
                   'reminder': 2*cadence_frequency,
                   'exposure_time': parameter.get('exposure_time', '')
            }
        form = SpecSchedulingForm(initial=initial)

        parameters.append({'observation_id': observation.id,
                           'obsgroup_id': obsgroup.id,
                           'target': target,
                           'names': target_names,
                           'facility': facility,
                           'proposal':  parameter.get('proposal', ''),
                           'observation_type': observation_type,
                           'cadence_strategy': cadence_strat,
                           'instrument': instrument,
                           'start': start + ' by ' + requested_str,
                           'comment': comment_str,
                           'reminder': end,
                           'user_id': user_id,
                           'case': case
                        })


    return {'observations': observation,
            'parameters': parameters,
            'form': form
    }


@register.inclusion_tag('custom_code/scheduling_list_with_form.html', takes_context=True)
def scheduling_list_with_form(context, observation, case='notpending'):
    facility = observation.facility
    
    # For now, we'll only worry about scheduling for LCO observations
    if facility != 'LCO':
        return {'observations': observation,
                'parameters': ''}
         
    obsgroup = observation.observationgroup_set.first()
    template_observation = obsgroup.observation_records.all().filter(observation_id='template').first()
    if not template_observation and case!='pending':
        obsset = obsgroup.observation_records.all()
        obsset = obsset.order_by('parameters__start')
        start = str(obsset.first().parameters['start']).replace('T', ' ')
        requested_str = ''
    else:
        if case == 'pending':
            template_observation = observation
        start = str(template_observation.parameters.get('sequence_start', '')).replace('T', ' ')
        if not start:
            start = str(template_observation.parameters.get('start', '')).replace('T', ' ')
        requested_str = str(template_observation.parameters.get('start_user', ''))
    
    return get_scheduling_form(observation, context['request'].user.id, start, requested_str, case=case)


@register.filter
def order_by_pending_requests(queryset): #, pagenumber):
    #queryset = queryset.exclude(status='CANCELED') 
    #queryset = queryset.filter(observation_id='template pending').order_by('id')
    queryset = ObservationRecord.objects.filter(observation_id='template pending')
    return queryset
    

@register.filter
def order_by_reminder_expired(queryset, pagenumber):
    queryset = queryset.exclude(status='CANCELED')
    from django.core.paginator import Paginator
    now = datetime.datetime.now()
   
    queryset = queryset.filter(parameters__reminder__lt=datetime.datetime.strftime(now, '%Y-%m-%dT%H:%M:%S'))
    queryset = queryset.order_by('parameters__reminder')

    paginator = Paginator(queryset, 25)
    page_number = pagenumber.strip('page=')
    page_obj = paginator.get_page(page_number)
    return page_obj
    #return queryset


@register.filter
def order_by_reminder_upcoming(queryset, pagenumber):
    queryset = queryset.exclude(status='CANCELED')
    from django.core.paginator import Paginator
    now = datetime.datetime.now()
   
    queryset = queryset.filter(parameters__reminder__gt=datetime.datetime.strftime(now, '%Y-%m-%dT%H:%M:%S')) 
    queryset = queryset.order_by('parameters__reminder')

    paginator = Paginator(queryset, 25)
    page_number = pagenumber.strip('page=')
    page_obj = paginator.get_page(page_number)
    return page_obj
    #return queryset


@register.inclusion_tag('custom_code/dash_spectra_page.html', takes_context=True)
def dash_spectra_page(context, target):
    request = context['request']
    try:
        z = TargetExtra.objects.filter(target_id=target.id, key='redshift').first().float_value
    except:
        z = 0

    ### Send the min and max flux values
    target_id = target.id
    spectral_dataproducts = ReducedDatum.objects.filter(target_id=target_id, data_type='spectroscopy').order_by('timestamp')
    if not spectral_dataproducts:
        return {'dash_context': {},
                'request': request
            }
    
    plot_list = []
    for i in range(len(spectral_dataproducts)):
    
        max_flux = 0
        min_flux = 0
        
        spectrum = spectral_dataproducts[i]
        datum = spectrum.value
        wavelength = []
        flux = []
        name = str(spectrum.timestamp).split(' ')[0]
        if datum.get('photon_flux'):
            wavelength = datum.get('wavelength')
            flux = datum.get('photon_flux')
        elif datum.get('flux'):
            wavelength = datum.get('wavelength')
            flux = datum.get('flux')
        else:
            for key, value in datum.items():
                wavelength.append(value['wavelength'])
                flux.append(float(value['flux']))
        if max(flux) > max_flux: max_flux = max(flux)
        if min(flux) < min_flux: min_flux = min(flux)

        snex_id_row = ReducedDatumExtra.objects.filter(data_type='spectroscopy', target_id=target_id, key='snex_id', value__icontains='"snex2_id": {}'.format(spectrum.id)).first()
        if snex_id_row:
            snex1_id = json.loads(snex_id_row.value)['snex_id']
            spec_extras_row = ReducedDatumExtra.objects.filter(data_type='spectroscopy', key='spec_extras', value__icontains='"snex_id": {}'.format(snex1_id)).first()
            if spec_extras_row:
                spec_extras = json.loads(spec_extras_row.value)
                if spec_extras.get('instrument', '') == 'en06':
                    spec_extras['site'] = '(OGG 2m)'
                    spec_extras['instrument'] += ' (FLOYDS)'
                elif spec_extras.get('instrument', '') == 'en12':
                    spec_extras['site'] = '(COJ 2m)'
                    spec_extras['instrument'] += ' (FLOYDS)'

                content_type_id = ContentType.objects.get(model='reduceddatum').id
                comments = Comment.objects.filter(object_pk=spectrum.id, content_type_id=content_type_id).order_by('id')
                comment_list = ['{}: {}'.format(User.objects.get(username=comment.user_name).first_name, comment.comment) for comment in comments]
                spec_extras['comments'] = comment_list
            
            else:
                spec_extras = {}
        elif spectrum.data_product_id:
            spec_extras_row = ReducedDatumExtra.objects.filter(data_type='spectroscopy', key='upload_extras', value__icontains='"data_product_id": {}'.format(spectrum.data_product_id)).first()
            if spec_extras_row:
                spec_extras = json.loads(spec_extras_row.value)
                if spec_extras.get('instrument', '') == 'en06':
                    spec_extras['site'] = '(OGG 2m)'
                    spec_extras['instrument'] += ' (FLOYDS)'
                elif spec_extras.get('instrument', '') == 'en12':
                    spec_extras['site'] = '(COJ 2m)'
                    spec_extras['instrument'] += ' (FLOYDS)'

                content_type_id = ContentType.objects.get(model='reduceddatum').id
                comments = Comment.objects.filter(object_pk=spectrum.id, content_type_id=content_type_id).order_by('id')
                comment_list = ['{}: {}'.format(User.objects.get(username=comment.user_name).first_name, comment.comment) for comment in comments]
                spec_extras['comments'] = comment_list
        else:
            spec_extras = {}

        plot_list.append({'dash_context': {'spectrum_id': {'value': spectrum.id},
                                           'target_redshift': {'value': z},
                                           'min-flux': {'value': min_flux},
                                           'max-flux': {'value': max_flux}
                                        },
                          'time': str(spectrum.timestamp).split('+')[0],
                          'spec_extras': spec_extras,
                          'spectrum': spectrum
                        })
    return {'plot_list': plot_list,
            'request': request}

@register.filter
def strip_trailing_zeros(value):
    try:
        return str(float(value))
    except:
        return value

@register.filter
def get_best_name(target):

    def find_name(namelist, n):
        for name in namelist:
            if n in name[:2].upper() and 'LAS' not in name[:5].upper():
                return name[:2].upper() + ' ' + name[2:]
        return False

    namelist = [target.name] + [alias.name for alias in target.aliases.all()]
    bestname = find_name(namelist, 'SN')
    if not bestname:
        bestname = find_name(namelist, 'AT')
    if not bestname:
        bestname = namelist[0]
    
    return bestname


@register.inclusion_tag('custom_code/display_group_list.html')
def display_group_list(target):
    groups = Group.objects.all()
    return {'target': target,
            'groups': groups
        }

@register.filter
def target_known_to(target):
    groups = get_groups_with_perms(target)
    return groups


@register.inclusion_tag('custom_code/reference_status.html')
def reference_status(target):
    old_status_query = TargetExtra.objects.filter(target=target, key='reference')
    if not old_status_query:
        old_status = 'Undetermined'
    else:
        old_status = old_status_query.first().value

    reference_form = ReferenceStatusForm(initial={'target': target.id,
                                                  'status': old_status})
    
    return {'object': target,
            'form': reference_form}


@register.inclusion_tag('custom_code/interested_persons.html')
def interested_persons(target, user, page):
    interested_persons_query = InterestedPersons.objects.filter(target=target)
    interested_persons = [u.user.get_full_name() for u in interested_persons_query]
    try:
        current_user_name = user.get_full_name()
    except:
        current_user_name = user
    
    interesting_list = TargetList.objects.filter(name='Interesting Targets').first()
    if not interesting_list:
        # Make a new list for interesting targets
        interesting_list = TargetList(name='Interesting Targets')
        interesting_list.save()
    
    interesting_list_id = int(interesting_list.id)
      
    return {'target': target,
            'interested_persons': interested_persons,
            'interesting_list_id': interesting_list_id,
            'user': current_user_name,
            'page': page
        }


@register.inclusion_tag('custom_code/partials/target_interest_button.html')
def target_interest_button(target, user, page):
    context = interested_persons(target, user, page)
    return context


@register.filter
def upcoming_observing_runs(targetlist):
    upcoming_runs = []
    today = datetime.date.today()
    try:
        for obj in targetlist:
            if obj.name == 'Interesting Targets':
                continue
            name = obj.name
            observing_run_datestr = name.split('_')[1]
            year = int(observing_run_datestr[:4])
            month = int(observing_run_datestr[4:6])
            day = int(observing_run_datestr[6:])
            observing_run_date = datetime.date(year, month, day)
            if today <= observing_run_date:
                upcoming_runs.append(obj)

        return upcoming_runs
    except:
        return targetlist


@register.filter
def past_observing_runs(targetlist):
    past_runs = []
    today = datetime.date.today()
    try:
        for obj in targetlist:
            if obj.name == 'Interesting Targets':
                continue
            name = obj.name
            observing_run_datestr = name.split('_')[1]
            year = int(observing_run_datestr[:4])
            month = int(observing_run_datestr[4:6])
            day = int(observing_run_datestr[6:])
            observing_run_date = datetime.date(year, month, day)
            if today > observing_run_date:
                past_runs.append(obj)

        return past_runs
    except Exception as e:
        print(e)
        return targetlist


@register.filter
def interesting_targets(targetlist):
    for obj in targetlist:
        if obj.name == 'Interesting Targets':
            return obj
    return []


@register.filter
def is_interesting(target):
    interesting_list = TargetList.objects.filter(name='Interesting Targets').first()
    if not interesting_list:
        return False
    if target in interesting_list.targets.all():
        return True
    else:
        return False


@register.filter
def get_other_observing_runs(targetlist):
    other_runs = []
    today = datetime.date.today()
    try:
        complement_targetlist = TargetList.objects.exclude(pk__in=targetlist.values_list('pk', flat=True))
        for obj in complement_targetlist:
            name = obj.name
            observing_run_datestr = name.split('_')[1]
            year = int(observing_run_datestr[:4])
            month = int(observing_run_datestr[4:6])
            day = int(observing_run_datestr[6:])
            observing_run_date = datetime.date(year, month, day)
            if today <= observing_run_date:
                other_runs.append(obj)

        return other_runs
    except:
        return []


@register.filter
def order_by_priority(targetlist):
    return targetlist.filter(targetextra__key='observing_run_priority').order_by('targetextra__value')


def get_lightcurve_params(target, key):
    query = TargetExtra.objects.filter(target=target, key=key).first()
    if query and query.value:
        value = json.loads(query.value)
        date = "{} ({})".format(value['date'], value['jd'])
        params = {'date': date,
                  'mag': str(value['mag']),
                  'filt': str(value['filt']),
                  'source': str(value['source'])
        }
    else:
        params = {}
    return params


@register.inclusion_tag('custom_code/target_details.html', takes_context=True)
def target_details(context, target):
    request = context['request']
    user = context['user']
    
    ### Get previously saved target information
    nondet_params = get_lightcurve_params(target, 'last_nondetection')
    det_params = get_lightcurve_params(target, 'first_detection')
    max_params = get_lightcurve_params(target, 'maximum')
    
    description_query = TargetExtra.objects.filter(target=target, key='target_description').first()
    if description_query:
        description = description_query.value
    else:
        description = ''
 
    return {'target': target,
            'request': request,
            'user': user,
            'last_nondetection': nondet_params,
            'first_detection': det_params,
            'maximum': max_params,
            'description': description}


@register.inclusion_tag('custom_code/image_slideshow.html', takes_context=True)
def image_slideshow(context, target):
    request = context['request']
    user = context['user']

    ### Get a list of all the image filenames for this target
    if not settings.DEBUG:
        #NOTE: Production
        try:
            filepaths, filenames, dates, teles, filters, exptimes, psfxs, psfys = run_hook('find_images_from_snex1', target.id, allimages=True)
        except:
            logger.info('Finding images in snex1 failed')
            return {'target': target,
                    'form': ThumbnailForm(initial={}, choices={'filenames': [('', 'No images found')]})} 
    else: 
        #NOTE: Development
        filepaths = ['/test/' for i in range(8)]
        filenames = ['coj1m011-fa12-20210216-0239-e91' for i in range(8)]
        dates = ['2020-08-03', '2020-08-02', '2020-08-01', '2020-07-31', '2020-07-30', 
                 '2020-07-29', '2020-07-28', '2020-07-27']
        teles = ['1m' for i in range(8)]
        filters = ['ip', 'ip', 'rp', 'rp', 'gp', 'gp', 'V', 'V']
        exptimes = [str(round(299.5)) + 's' for i in range(8)]
        psfxs = [9999 for i in range(8)]
        psfys = [9999 for i in range(8)]
    
    if not filenames:
        return {'target': target,
                'form': ThumbnailForm(initial={}, choices={'filenames': [('', 'No images found')]})}
    
    thumbdict = [(json.dumps({'filename': filenames[i],
                   'filepath': filepaths[i],
                   'date': dates[i],
                   'tele': teles[i],
                   'filter': filters[i],
                   'exptime': exptimes[i],
                   'psfx': psfxs[i],
                   'psfy': psfys[i]
                }),
                #filenames[i]) for i in range(len(filenames))]
                '{} ({} {})'.format(dates[i], filters[i], exptimes[i])) for i in range(len(filenames))]

    initial = {'filenames': filenames[0],
               'zoom': 1.0,
               'sigma': 4.0
            }

    choices = {'filenames': thumbdict}
    thumbnailform = ThumbnailForm(initial=initial, choices=choices)

    ### Make the initial thumbnail
    if psfxs[0] < 9999 and psfys[0] < 9999:
        f = make_thumb(['data/fits/'+filepaths[0]+filenames[0]+'.fits'], grow=1.0, x=psfxs[0], y=psfys[0], ticks=True)
    else:
        f = make_thumb(['data/fits/'+filepaths[0]+filenames[0]+'.fits'], grow=1.0, x=1024, y=1024, ticks=False)

    with open('data/thumbs/'+f[0], 'rb') as imagefile:        
        b64_image = base64.b64encode(imagefile.read())
        thumb = b64_image

    return {'target': target,
            'form': thumbnailform,
            'thumb': b64_image.decode('utf-8'),
            'telescope': teles[0],
            'instrument': filenames[0].split('-')[1][:2],
            'filter': filters[0],
            'exptime': exptimes[0]}


@register.inclusion_tag('custom_code/lightcurve_collapse.html')
def lightcurve_fits(target, user, filt=False, days=None):
    
    filter_translate = {'U': 'U', 'B': 'B', 'V': 'V',
        'g': 'g', 'gp': 'g', 'r': 'r', 'rp': 'r', 'i': 'i', 'ip': 'i',
        'g_ZTF': 'g_ZTF', 'r_ZTF': 'r_ZTF', 'i_ZTF': 'i_ZTF', 'UVW2': 'UVW2', 'UVM2': 'UVM2', 
        'UVW1': 'UVW1'}
    plot_data = generic_lightcurve_plot(target, user)     
    photometry_data = {}

    if settings.TARGET_PERMISSIONS_ONLY:
        datums = ReducedDatum.objects.filter(target=target, data_type=settings.DATA_PRODUCT_TYPES['photometry'][0])
    else:
        datums = get_objects_for_user(user,
                                      'tom_dataproducts.view_reduceddatum',
                                      klass=ReducedDatum.objects.filter(
                                        target=target,
                                        data_type=settings.DATA_PRODUCT_TYPES['photometry'][0]))

    for rd in datums:
        value = rd.value
        if not value:  # empty
            continue
        if isinstance(value, str):
            value = json.loads(value)

        current_filt = filter_translate.get(value.get('filter', ''), '')
   
        photometry_data.setdefault(current_filt, {})
        photometry_data[current_filt].setdefault('time', []).append(rd.timestamp)
        photometry_data[current_filt].setdefault('magnitude', []).append(value.get('magnitude',None))
        photometry_data[current_filt].setdefault('error', []).append(value.get('error', None))        

    plot_data = [
        go.Scatter(
            x=filter_values['time'],
            y=filter_values['magnitude'], mode='markers',
            marker=dict(color=get_color(filter_name, filter_translate)),
            name=filter_translate.get(filter_name, ''),
            error_y=dict(
                type='data',
                array=filter_values['error'],
                visible=True,
                color=get_color(filter_name, filter_translate)
            )
        ) for filter_name, filter_values in photometry_data.items()] 
     
    layout = go.Layout(
        xaxis=dict(gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        yaxis=dict(autorange='reversed',gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        margin=dict(l=30, r=10, b=100, t=40),
        hovermode='closest',
        plot_bgcolor='white'
    )
    
    if not plot_data:
        return {
            'target': target,
            'plot': 'No photometry for this target yet.',
            'max': '',
            'mag': '',
            'filt': ''
        }
    
        ### Fit a parabola to the lightcurve to find the max
    if filt and filt in photometry_data.keys(): # User has specified a filter to fit
        photometry_to_fit = photometry_data[filt]
    
    elif filt and filt not in photometry_data.keys(): # No photometry for this filter
        return {
            'target': target,
            'plot': offline.plot(go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False),
            'max': '',
            'mag': '',
            'filt': ''
        }
    
    else:
        filtlist = list(photometry_data.keys())
        lens = []
        for f in filtlist:
            lens.append(len(photometry_data[f]['magnitude']))
        filt = filtlist[lens.index(max(lens))]
        photometry_to_fit = photometry_data[filt]

    start_date = min(photometry_to_fit['time'])
    start_jd = Time(start_date, scale='utc').jd
   
    times = photometry_to_fit['time']
    mags = []
    errs = []
    jds = []

    if not days:
        days_to_fit = 20
    else:
        days_to_fit = days

    for date in times:
        if Time(date, scale='utc').jd < start_jd + days_to_fit:
            jds.append(float(Time(date, scale='utc').jd))
            mags.append(photometry_to_fit['magnitude'][times.index(date)])
            errs.append(photometry_to_fit['error'][times.index(date)])
    try:
        A, B, C = np.polyfit(jds, mags, 2, w=1/(np.asarray(errs)))
        fit_jds = np.linspace(min(jds), max(jds), 100)
        quadratic_fit = A*fit_jds**2 + B*fit_jds + C

        plot_data.append(
            go.Scatter(
                x=Time(fit_jds, format='jd', scale='utc').isot,
                y=quadratic_fit, mode='lines',
                marker=dict(color='gray'),
                name='n=2 fit'
            )
        )

        max_mag = round(min(quadratic_fit), 2)
        max_jd = fit_jds[np.argmin(quadratic_fit)]
        max_date = Time(max_jd, format='jd', scale='utc').isot

        plot_data.append(
            go.Scatter(
                x=[max_date],
                y=[max_mag],
                mode='markers',
                marker=dict(color='gold', size=15, symbol='star', line=dict(color='black', width=2)),
                name='Maximum'
            )
        )
        maximum = round(abs(B/(2*A)), 2)
    except Exception as e:
        logger.info(e)
        logger.info('Quadratic light curve fit failed for target {}'.format(target.id))
        maximum = ''

    return {
        'target': target,
        'plot': offline.plot(go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False),
        'max': maximum,
        'mag': max_mag,
        'filt': filt
    }


@register.inclusion_tag('custom_code/lightcurve_collapse.html')
def lightcurve_with_extras(target, user):
    
    filter_translate = {'U': 'U', 'B': 'B', 'V': 'V',
        'g': 'g', 'gp': 'g', 'r': 'r', 'rp': 'r', 'i': 'i', 'ip': 'i',
        'g_ZTF': 'g_ZTF', 'r_ZTF': 'r_ZTF', 'i_ZTF': 'i_ZTF', 'UVW2': 'UVW2', 'UVM2': 'UVM2', 
        'UVW1': 'UVW1'}
    plot_data = generic_lightcurve_plot(target, user)         
    spec = ReducedDatum.objects.filter(target=target, data_type='spectroscopy')

    layout = go.Layout(
        xaxis=dict(gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        yaxis=dict(autorange='reversed',gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        margin=dict(l=30, r=10, b=100, t=40),
        hovermode='closest',
        plot_bgcolor='white',
        shapes=[
            dict(
                type='line',
                yref='paper',
                y0=0,
                y1=1,
                xref='x',
                x0=s.timestamp,
                x1=s.timestamp,
                opacity=0.2,
                line=dict(color='black', dash='dash'),
            ) for s in spec]
        #height=500,
        #width=500
    )

    ## Check for last nondetection, first detection, and max in the database
    symbols = {'last_nondetection': 'arrow-down', 'first_detection': 'arrow-up', 'maximum': 'star'}
    names = {'last_nondetection': 'Last non-detection', 'first_detection': 'First detection', 'maximum': 'Maximum'}
    for key in ['last_nondetection', 'first_detection', 'maximum']:
        query = TargetExtra.objects.filter(target=target, key=key).first()
        if query and query.value:
            value = json.loads(query.value)
            jd = value.get('jd', None)
            if jd:
                plot_data.append(
                    go.Scatter(
                        x=[Time(float(jd), format='jd', scale='utc').isot],
                        y=[float(value['mag'])], mode='markers',
                        marker=dict(color=get_color(value['filt'], filter_translate), size=12, symbol=symbols[key]),
                        name=names[key]
                    )
                )

    if plot_data:
      return {
          'target': target,
          'plot': offline.plot(go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False)
      }
    else:
        return {
            'target': target,
            'plot': 'No photometry for this target yet.'
        }


@register.inclusion_tag('custom_code/thumbnail.html', takes_context=True)
def test_display_thumbnail(context, target):
    
    from os import listdir
    from os.path import isfile, join
    
    if not settings.DEBUG:
        #NOTE: Production
        try:
            filepaths, filenames, dates, teles, filters, exptimes, psfxs, psfys = run_hook('find_images_from_snex1', target.id)
        except:
            logger.info('Finding images in snex1 failed')
            return {'top_images': [],
                    'bottom_images': []}

    else:
 
        #NOTE: Development
        filepaths = ['/test/' for i in range(8)]
        filenames = ['coj1m011-fa12-20210216-0239-e91' for i in range(8)]
        dates = ['2020-08-03', '2020-08-02', '2020-08-01', '2020-07-31', '2020-07-30', 
                 '2020-07-29', '2020-07-28', '2020-07-27']
        teles = ['1m' for i in range(8)]
        filters = ['ip', 'ip', 'rp', 'rp', 'gp', 'gp', 'V', 'V']
        exptimes = [str(round(299.5)) + 's' for i in range(8)]
        psfxs = [9999 for i in range(8)]
        psfys = [9999 for i in range(8)]

    if not filenames:
        return {'top_images': [],
                'bottom_images': []}

    thumbs = [f for f in listdir('data/thumbs/') if isfile(join('data/thumbs/', f))]
    top_images = []
    bottom_images = [] 
    sites = [f[:3].upper() for f in filenames]
    
    thumbfiles = []
    thumbdates = []
    thumbteles = []
    thumbsites = []
    thumbfilters = []
    thumbexptimes = []

    for i in range(len(filenames)):
        currentfile = filenames[i]
        if any(currentfile in f and 'grow' not in f for f in thumbs):
            matchingfiles = [f for f in thumbs if f.startswith(currentfile) and 'grow' not in f]
            if matchingfiles:
                thumbfiles.append(matchingfiles[0])
        else:
            # Generate the thumbnail and save the image
            if psfxs[i] < 9999 and psfys[i] < 9999:
                f = make_thumb(['data/fits/'+filepaths[i]+currentfile+'.fits'], grow=1.0, x=psfxs[i], y=psfys[i], ticks=True)
            else:
                f = make_thumb(['data/fits/'+filepaths[i]+currentfile+'.fits'], grow=1.0, x=1024, y=1024, ticks=False)
            thumbfiles.append(f[0])
        
        thumbdates.append(dates[i])
        thumbteles.append(teles[i])
        thumbsites.append(sites[i])
        thumbfilters.append(filters[i])
        thumbexptimes.append(exptimes[i])
    
    halfway = round(len(thumbfiles)/2)
    
    for i in range(len(thumbfiles)):
        with open('data/thumbs/'+thumbfiles[i], 'rb') as imagefile:        
            b64_image = base64.b64encode(imagefile.read())
            label = '{} {} {} {} {}'.format(thumbdates[i], thumbsites[i], thumbteles[i], thumbfilters[i], thumbexptimes[i])
            if i < halfway:
                top_images.append({'image': b64_image.decode('utf-8'),
                                   'label': label,
                                })
            else:
                bottom_images.append({'image': b64_image.decode('utf-8'),
                                      'label': label
                                      })

    return {'top_images': top_images,
            'bottom_images': bottom_images}


@register.filter
def urgency_converter(urgency):
    return round(urgency.total_seconds()/(24*60*60), 1)


@register.inclusion_tag('custom_code/lightcurve_collapse.html')
def broker_target_lightcurve(target):

    filter_translate = {'U': 'U', 'B': 'B', 'V': 'V',
        'g': 'g', 'gp': 'g', 'r': 'r', 'rp': 'r', 'i': 'i', 'ip': 'i',
        'g_ZTF': 'g_ZTF', 'r_ZTF': 'r_ZTF', 'i_ZTF': 'i_ZTF', 'UVW2': 'UVW2', 'UVM2': 'UVM2',
        'UVW1': 'UVW1', 'cyan': 'cyan', 'orange': 'orange'}
     
    photometry_data = {}
    nondetection_data = {}
    
    detections = json.loads(target.detections)

    for filt in detections:
        if not detections[filt]:
            continue

        photometry_data.setdefault(filt, {})

        for mjd, phot in detections[filt].items():
            photometry_data[filt].setdefault('time', []).append(Time(mjd, format='mjd').to_value('iso'))
            photometry_data[filt].setdefault('magnitude', []).append(phot[0])
            photometry_data[filt].setdefault('magerr', []).append(phot[1])

    plot_data = [
        go.Scatter(
            x=filter_values['time'],
            y=filter_values['magnitude'], mode='markers',
            marker=dict(color=get_color(filter_name, filter_translate)),
            name=filter_translate.get(filter_name, filter_name),
            error_y=dict(
                type='data',
                array=filter_values['magerr'],
                visible=True,
                color=get_color(filter_name, filter_translate)
            )
        ) for filter_name, filter_values in photometry_data.items()] 


    nondetections = json.loads(target.nondetections)

    for filt in nondetections:
        if not nondetections[filt]:
            continue
        nondetection_data.setdefault(filt, {})

        for mjd, mag in nondetections[filt].items():
            nondetection_data[filt].setdefault('time', []).append(Time(mjd, format='mjd').to_value('iso'))
            nondetection_data[filt].setdefault('magnitude', []).append(mag)

    plot_data += [
        go.Scatter(
            x=filter_values['time'],
            y=filter_values['magnitude'], mode='markers',
            marker=dict(color=get_color(filter_name, filter_translate), symbol='arrow-down'),
            name=filter_translate.get(filter_name, filter_name) + ' upper limit',
        ) for filter_name, filter_values in nondetection_data.items()] 

    layout = go.Layout(
        xaxis=dict(gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        yaxis=dict(autorange='reversed',gridcolor='#D3D3D3',showline=True,linecolor='#D3D3D3',mirror=True),
        margin=dict(l=30, r=10, b=10, t=40),
        hovermode='closest',
        plot_bgcolor='white',
        height=300,
        width=500
    )


    if plot_data:
      return {
          'target': target,
          'plot': offline.plot(go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False)
      }
    else:
        return {
            'target': target,
            'plot': 'No photometry for this target yet.'
        }


@register.inclusion_tag('tom_dataproducts/partials/photometry_datalist_for_target.html', takes_context=True)
def snex2_get_photometry_data(context, target):

    user = context['request'].user
    photometry = get_objects_for_user(user,
                                  'tom_dataproducts.view_reduceddatum',
                                  klass=ReducedDatum.objects.filter(
                                    target=target,
                                    data_type=settings.DATA_PRODUCT_TYPES['photometry'][0],
                                    value__has_key='filter')).order_by('timestamp')
    data = []
    for reduced_datum in photometry:
        rd_data = {'id': reduced_datum.pk,
                   'timestamp': reduced_datum.timestamp,
                   'source': reduced_datum.source_name,
                   'filter': reduced_datum.value.get('filter', ''),
                   'telescope': reduced_datum.value.get('telescope', ''),
                   'error': reduced_datum.value.get('error', '')
                   }

        if 'limit' in reduced_datum.value.keys():
            rd_data['magnitude'] = reduced_datum.value['limit']
            rd_data['limit'] = True
        else:
            rd_data['magnitude'] = reduced_datum.value['magnitude']
            rd_data['limit'] = False

        messages = []
        for message in reduced_datum.message.all():
            if message.exchange_status == 'published':
                messages.append(message.exchange_status + ' to ' + message.topic)
            else:
                messages.append(message.exchange_status + ' from ' + message.topic)
        rd_data['messages'] = messages

        data.append(rd_data)

    initial = {'submitter': user,
               'target': target,
               'data_type': 'photometry',
               'share_title': f"Updated data for {target.name} from {getattr(settings, 'TOM_NAME', 'TOM Toolkit')}.",
               }
    form = DataShareForm(initial=initial)
    form.fields['share_title'].widget = forms.HiddenInput()
    form.fields['data_type'].widget = forms.HiddenInput()

    context = {'data': data,
               'target': target,
               'target_data_share_form': form,
               'sharing_destinations': form.fields['share_destination'].choices}
    return context


@register.inclusion_tag('tom_dataproducts/partials/share_target_data.html')
def snex2_share_data(target, user):
    """
    Publish data to Hermes
    """
    initial = {'submitter': user,
               'target': target,
               'share_title': f"Updated data for {target.name} from {getattr(settings, 'TOM_NAME', 'TOM Toolkit')}.",
               }
    form = DataShareForm(initial=initial)
    form.fields['share_title'].widget = forms.HiddenInput()

    context = {'target': target,
               'target_data_share_form': form,
               'sharing_destinations': form.fields['share_destination'].choices}
    return context


@register.inclusion_tag('custom_code/partials/time_usage_bars.html', takes_context=True)
def time_usage_bars(context, telescope):
    
    tu = TimeUsed.objects.filter(telescope_class=telescope).order_by('-id').first()
    
    total_time_used = tu.std_time_used + tu.tc_time_used + tu.rr_time_used
    total_time_allocated = tu.std_time_allocated + tu.tc_time_allocated + tu.rr_time_allocated
    total_frac_used = total_time_used/total_time_allocated
    
    if total_frac_used > tu.frac_of_semester:
        barcolor = 'red'
    else:
        barcolor = '#004459'
    
    barwidth = int(100*tu.frac_of_semester)
    usedbarwidth = max(int(total_frac_used*100.0), 1)

    tooltip = "Standard time: {} of {} hours ({}%)\n".format(
            round(tu.std_time_used, 2), round(tu.std_time_allocated, 2),
            round(tu.std_time_used/tu.std_time_allocated * 100, 2))

    if tu.tc_time_allocated > 0.0:
        tooltip += "TC time: {} of {} hours ({}%)\n".format(
                round(tu.tc_time_used, 2), round(tu.tc_time_allocated, 2),
                round(tu.tc_time_used/tu.tc_time_allocated * 100, 2))
    else:
        tooltip += "TC time: {} of {} hours\n".format(
                round(tu.tc_time_used, 2), round(tu.tc_time_allocated, 2))
    
    if tu.rr_time_allocated > 0.0:
        tooltip += "RR time: {} of {} hours ({}%)\n".format(
            round(tu.rr_time_used, 2), round(tu.rr_time_allocated, 2),
            round(tu.rr_time_used/tu.rr_time_allocated * 100, 2))
    else:
        tooltip += "RR time: {} of {} hours\n".format(
                round(tu.rr_time_used, 2), round(tu.rr_time_allocated, 2))

    tooltip += "[We're currently {}% through the semester]".format(round(tu.frac_of_semester*100, 2))
    
    return {'telescope': telescope.replace('0', '').lower(),
            'barwidth': barwidth,
            'barcolor': barcolor,
            'usedbarwidth': usedbarwidth,
            'tooltip': tooltip,
    }
 
