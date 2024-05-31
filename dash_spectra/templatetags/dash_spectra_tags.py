from tom_dataproducts.models import ReducedDatum
from tom_targets.models import TargetExtra
from django import template

register = template.Library()


def calculate_flux_extrema(spectrum, max_flux=0, min_flux=0):
    """
    Calculates the maximum and minimum flux from a spectrum
    Used to set the yaxis range on dash spectra plots
    Input: a Spectrum ReducedDatum object
           optional: a starting maximum and minimum flux
    """
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

    return max_flux, min_flux


@register.inclusion_tag('dash_spectra/dash_spectra.html', takes_context=True)
def dash_spectra(context, target, individual=False):
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

    if individual:

        plot_list = []
        for i in range(len(spectral_dataproducts)):

            spectrum = spectral_dataproducts[i]
            max_flux, min_flux = calculate_flux_extrema(spectrum)

            #snex_id_row = ReducedDatumExtra.objects.filter(data_type='spectroscopy', target_id=target_id, key='snex_id', value__icontains='"snex2_id": {}'.format(spectrum.id)).first()
            #if snex_id_row:
            #    snex1_id = json.loads(snex_id_row.value)['snex_id']
            #    spec_extras_row = ReducedDatumExtra.objects.filter(data_type='spectroscopy', key='spec_extras', value__icontains='"snex_id": {}'.format(snex1_id)).first()
            #    if spec_extras_row:
            #        spec_extras = json.loads(spec_extras_row.value)
            #        if spec_extras.get('instrument', '') == 'en06':
            #            spec_extras['site'] = '(OGG 2m)'
            #            spec_extras['instrument'] += ' (FLOYDS)'

            #        content_type_id = ContentType.objects.get(model='reduceddatum').id
            #        comments = Comment.objects.filter(object_pk=spectrum.id, content_type_id=content_type_id).order_by('id')
            #        comment_list = ['{}: {}'.format(User.objects.get(username=comment.user_name).first_name, comment.comment) for comment in comments]
            #        spec_extras['comments'] = comment_list

            #    else:
            #        spec_extras = {}
            #else:
            #    spec_extras = {}

            plot_list.append({'dash_context': {'spectrum_id': {'value': spectrum.id},
                                               'target_redshift': {'value': z},
                                               'min-flux': {'value': min_flux},
                                               'max-flux': {'value': max_flux}
                                            },
                              'time': str(spectrum.timestamp).split('+')[0],
            #                  'spec_extras': spec_extras,
                              'spectrum': spectrum
                            })

        return {'plot_list': plot_list,
                'request': request}
    
    else:
    
        max_flux, min_flux = 0, 0
        
        for i in range(len(spectral_dataproducts)):
            spectrum = spectral_dataproducts[i]
            max_flux, min_flux = calculate_flux_extrema(spectrum, max_flux=max_flux, min_flux=min_flux)

        dash_context = {'target_id': {'value': target.id},
                        'target_redshift': {'value': z},
                        'min-flux': {'value': min_flux},
                        'max-flux': {'value': max_flux}
                    }
    
        return {'dash_context': dash_context,
                'request': request}


#@register.inclusion_tag('dash_spectra/dash_spectra_page.html', takes_context=True)
#def dash_spectra_page(context, target):
#    request = context['request']
#    try:
#        z = TargetExtra.objects.filter(target_id=target.id, key='redshift').first().float_value
#    except:
#        z = 0
#
#    ### Send the min and max flux values
#    target_id = target.id
#    spectral_dataproducts = ReducedDatum.objects.filter(target_id=target_id, data_type='spectroscopy').order_by('timestamp')
#    if not spectral_dataproducts:
#        return {'dash_context': {},
#                'request': request
#            }
#
#    plot_list = []
#    for i in range(len(spectral_dataproducts)):
#
#        spectrum = spectral_dataproducts[i]
#        max_flux, min_flux = calculate_flux_extrema(spectrum)
#
#        #snex_id_row = ReducedDatumExtra.objects.filter(data_type='spectroscopy', target_id=target_id, key='snex_id', value__icontains='"snex2_id": {}'.format(spectrum.id)).first()
#        #if snex_id_row:
#        #    snex1_id = json.loads(snex_id_row.value)['snex_id']
#        #    spec_extras_row = ReducedDatumExtra.objects.filter(data_type='spectroscopy', key='spec_extras', value__icontains='"snex_id": {}'.format(snex1_id)).first()
#        #    if spec_extras_row:
#        #        spec_extras = json.loads(spec_extras_row.value)
#        #        if spec_extras.get('instrument', '') == 'en06':
#        #            spec_extras['site'] = '(OGG 2m)'
#        #            spec_extras['instrument'] += ' (FLOYDS)'
#
#        #        content_type_id = ContentType.objects.get(model='reduceddatum').id
#        #        comments = Comment.objects.filter(object_pk=spectrum.id, content_type_id=content_type_id).order_by('id')
#        #        comment_list = ['{}: {}'.format(User.objects.get(username=comment.user_name).first_name, comment.comment) for comment in comments]
#        #        spec_extras['comments'] = comment_list
#
#        #    else:
#        #        spec_extras = {}
#        #else:
#        #    spec_extras = {}
#
#        plot_list.append({'dash_context': {'spectrum_id': {'value': spectrum.id},
#                                           'target_redshift': {'value': z},
#                                           'min-flux': {'value': min_flux},
#                                           'max-flux': {'value': max_flux}
#                                        },
#                          'time': str(spectrum.timestamp).split('+')[0],
#        #                  'spec_extras': spec_extras,
#                          'spectrum': spectrum
#                        })
#    return {'plot_list': plot_list,
#            'request': request}
#
