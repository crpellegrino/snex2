from dash import dcc, html
from dash.dependencies import Input, Output, State
import dash_bootstrap_components as dbc
import plotly.graph_objs as go
import numpy as np
import json
from statistics import median

from django_plotly_dash import DjangoDash
from django.conf import settings
from tom_dataproducts.models import ReducedDatum
from tom_targets.models import Target, TargetExtra
from .util import bin_spectra
from django.db.models import Q
from django.templatetags.static import static
import logging

logger = logging.getLogger(__name__)

external_stylesheets = [dbc.themes.BOOTSTRAP]
app = DjangoDash(name='Spectra_Class_Individual', id='spectrum_id', add_bootstrap_links=True, suppress_callback_exceptions=True)
app.css.append_css({'external_url': static('custom_code/css/dash.css')})


class SpectraIndividual:

    params = [
        'Redshift', 'Velocity (km/s)'
    ]
    
    elements = settings.DASH_SPECTRA_PLOTS['elements']
    
    custom_elements = settings.DASH_SPECTRA_PLOTS['custom_lines']

    def __init__(self, app=None):
        
        self.app = app
        self.callbacks(self.app)
        self.layout = self.make_layout()
        app.layout = self.layout


    def make_layout(self):
        columns = [{'id': p, 'name': p} for p in self.params]
        columns.append({'id': 'Element', 'name': 'Element', 'editable': False})
        columns.insert(0, columns.pop())
        
        table_body_one =[html.Tbody([])]
        table_body_two =[html.Tbody([])]
        
        return html.Div([
            dbc.Row([
                dbc.Col(
                    dbc.Row([
                        dcc.Graph(id='table-editing-simple-output',
                                  figure = {'layout' : {'height': 400,
                                                        'width': 650,
                                                        'margin': {'l': 60, 'b': 30, 'r': 60, 't': 10},
                                                        'yaxis': {'type': 'linear', 'tickformat': '.1e'},
                                                        'xaxis': {'showgrid': False},
                                                        'legend': {'x': 0.85, 'y': 1.0},
                                                        },
                                            'data' : []#[go.Scatter({'x': [], 'y': []})]
                                        },
                        ),
                        dcc.Input(id='spectrum_id', type='hidden', value=0),
                        dcc.Input(id='target_redshift', type='hidden', value=0),
                        dcc.Input(id='min-flux', type='hidden', value=0),
                        dcc.Input(id='max-flux', type='hidden', value=0),
                        dbc.Row([
                            html.Div('Binning Factor: ', style={'color': 'black', 'fontSize': 18}),
                            dcc.Input(id='bin-factor', type='number', value=5, size=2, style={'width': '50px'}),
                        ], style={'margin-left': '5%'}),
                    ]),
                ),
                dbc.Col(
                    dbc.Row([
                        html.Div([
                            dcc.Checklist(
                                id='line-plotting-checklist',
                                options=[{'label': 'Show line plotting interface', 'value': 'display'}],
                                value='',
                                style={'fontSize': 18}
                            ),
                            html.Div(
                                children=[],
                                id='checked-rows',
                                style={'display': 'none'}
                            ),
                            html.Div(
                                children=[
                                    dbc.Row([
                                        dbc.Table(
                                            html.Tbody([
                                                html.Tr([
                                                    html.Td(
                                                        dbc.Table(table_body_one, bordered=True),
                                                    ),
                                                    html.Td(
                                                        dbc.Table(table_body_two, bordered=True),
                                                    )
                                                ]),
                                            ])
                                        )
                                    ])
                                ],
                                id='table-container-div',
                                style={'display': 'none'}
                            ),
                            dcc.Checklist(
                                id='compare-spectra-checklist',
                                options=[{'label': 'Compare this spectrum to another object?', 'value': 'display'}],
                                value='',
                                style={'fontSize': 18}
                            ),
                            html.Div([
                                html.Form(
                                    autoComplete='off',
                                    children=[ 
                                        dcc.Dropdown(
                                            options=[{'label': '', 'value': ''}],
                                            value='',
                                            placeholder='Search for a target',
                                            id='spectra-compare-dropdown',
                                            style={'z-index': '10'}
                                        )
                                    ],
                                    id='spectra-compare-results',
                                    style={'display': 'none'}
                                )
                            ]),
                            dcc.Checklist(
                                id='mask-lines-checklist',
                                options=[{'label': 'Mask galaxy emission lines', 'value': 'mask'}],
                                value='',
                                style={'fontSize': 18}
                            ),
                        ]),
                    ])
                )
            ]),
        ], style={'padding-bottom': '0px'})


    def callbacks(self, app):
        @app.callback(
            Output('spectra-compare-dropdown', 'options'),
            [Input('spectra-compare-dropdown', 'search_value'),
             State('spectra-compare-dropdown', 'value')])
        def get_target_list(value, existing, *args, **kwargs):
            if existing:
                target_match_list = Target.objects.filter(name=existing)
                if not target_match_list.first():
                    target_match_list = Target.objects.filter(aliases__name__icontains=existing)
                    names = []
                    for target in target_match_list:
                        names += [{'label': n, 'value': n} for n in target.names if n==existing]
                        return names
                else:
                    return [{'label': target.name, 'value': target.name} for target in target_match_list]
            
            elif value:
                target_match_list = Target.objects.filter(Q(name__icontains=value) | Q(aliases__name__icontains=value)).distinct()
            else:
                target_match_list = Target.objects.none()
            names = [{'label': '', 'value': ''}]
            for target in target_match_list:
                names += [{'label': n, 'value': n} for n in target.names]
            return names


        @app.callback(
            Output('table-container-div', 'style'),
            [Input('line-plotting-checklist', 'value')])
        def show_table(value, *args, **kwargs):
            if 'display' in value:
                return {'display': 'block'}
            else:
                return {'display': 'none'}
        
        
        @app.callback(
            Output('spectra-compare-results', 'style'),
            [Input('compare-spectra-checklist', 'value')])
        def show_compare(value, *args, **kwargs):
            if 'display' in value:
                return {'display': 'block'}
            else:
                return {'display': 'none'}
        
        
        @app.callback(
            Output('checked-rows', 'children'),
            [Input('standalone-checkbox-'+elem.replace(' ', '-'), 'checked') for elem in self.elements]+[Input('standalone-checkbox-'+c, 'checked') for c in self.custom_elements] + [Input('v-'+elem.replace(' ', '-'), 'value') for elem in self.elements]+[Input('v-'+c, 'value') for c in self.custom_elements] + [Input('z-'+elem.replace(' ', '-'), 'value') for elem in self.elements]+[Input('z-'+c, 'value') for c in self.custom_elements] + [Input('lambda-'+c, 'value') for c in self.custom_elements])
        def checked_boxes(*args, **kwargs):
            
            elt_rows = 0
            for elt in self.elements:
                elt_rows += 1

            custom_rows = 0
            for elt in self.custom_elements:
                custom_rows += 1
            
            all_rows = elt_rows + custom_rows
            checked_rows = []
            for i in range(elt_rows):
                if args[i]:
                    elem = list(self.elements.keys())[i]
                    checked_rows.append(json.dumps({elem: {'waves': self.elements[elem]['waves'],
                                                           'redshift': args[i+2*all_rows],
                                                           'velocity': args[i+all_rows],
                                                           'color': self.elements[elem]['color']
                                                        } 
                                                    })
                                                )
            for i in range(custom_rows):
                if args[elt_rows+i]:
                    elem = list(self.custom_elements.keys())[i]
                    checked_rows.append(json.dumps({elem: {'waves': [args[i-custom_rows]],
                                                           'redshift': args[i-2*custom_rows],
                                                           'velocity': args[i+all_rows+elt_rows],
                                                           'color': self.custom_elements[elem]['color']
                                                        }
                                                    })
                                                )
            return checked_rows
        
        
        @app.callback(
            Output('table-container-div', 'children'),
            [Input('target_redshift', 'value')])
        def change_redshift(z, *args, **kwargs):
            elem_input_array = []

            total_rows = len(self.elements.keys()) + len(self.custom_elements.keys())
            for elem in list(self.elements.keys())[:int(total_rows/2)]:
                row = html.Tr([
                    html.Td(
                        dbc.Checkbox(id='standalone-checkbox-'+elem.replace(' ', '-')),
                        style={"padding-left": "1rem"},
                    ),
                    html.Td(
                        elem,
                        style={"font-size": "12px"}
                    ),
                    html.Td(
                       dbc.Badge(
                           '__',#elem,
                           color=self.elements[elem]['color']
                        )
                    ),
                    html.Td(
                        dbc.Input(
                            id='z-'+elem.replace(' ', '-'),
                            value=z,
                            type='number',
                            min=0,
                            max=10,
                            step=0.0000001,
                            placeholder='z',
                            style={"font-size": "12px", "width": "70px"}
                        )
                    ),
                    html.Td(
                        dbc.Input(
                            id='v-'+elem.replace(' ', '-'),
                            type='number',
                            placeholder='v = 0 km/s',
                            style={"font-size": "12px", "width": "100px"}
                            #value=0
                        ),
                        colSpan=2,
                    ),
                ], style={'padding': '0rem'})
                elem_input_array.append(row)
            table_body_one = [html.Tbody(elem_input_array)]
            
            elem_input_array = []
            for elem in list(self.elements.keys())[int(total_rows/2):]:
                row = html.Tr([
                    html.Td(
                        dbc.Checkbox(id='standalone-checkbox-'+elem.replace(' ', '-'))
                    ),
                    html.Td(
                        elem,
                        style={"font-size": "12px"}
                    ),
                    html.Td(
                       dbc.Badge(
                           '__',#elem,
                           color=self.elements[elem]['color']
                        )
                    ),
                    html.Td(
                        dbc.Input(
                            id='z-'+elem.replace(' ', '-'),
                            value=z,
                            type='number',
                            min=0,
                            max=10,
                            step=0.0000001,
                            placeholder='z',
                            style={"font-size": "12px", "width": "70px"}
                        )
                    ),
                    html.Td(
                        dbc.Input(
                            id='v-'+elem.replace(' ', '-'),
                            type='number',
                            placeholder='v = 0 km/s',
                            style={"font-size": "12px", "width": "100px"}
                            #value=0
                        ),
                        colSpan=2,
                    ),
                ])
                elem_input_array.append(row)

            for elem in list(self.custom_elements.keys()):
                row = html.Tr([
                    html.Td(
                        dbc.Checkbox(id='standalone-checkbox-'+elem)
                    ),
                    html.Td(
                        html.Div([
                            dbc.Badge(
                                '__',
                                color=self.custom_elements[elem]['color']
                            ),
                            dbc.Input(
                                id='lambda-'+elem,
                                type='number',
                                min=0,
                                max=1e5,
                                step=0.1,
                                placeholder='Wl.',
                                style={"font-size": "12px", "width": "70px"}
                            )
                        ]),
                        colSpan=2
                    ),
                    html.Td(
                        dbc.Input(
                            id='z-'+elem,
                            type='number',
                            min=0,
                            max=10,
                            step=0.00000001,
                            placeholder='z',
                            value=z,
                            style={"font-size": "12px", "width": "70px"}
                        )
                    ),
                    html.Td(
                        dbc.Input(
                            id='v-'+elem,
                            type='number',
                            placeholder='v = 0 km/s',
                            style={"font-size": "12px", "width": "100px"}
                        )
                    )
                ])
                elem_input_array.append(row)
            
            table_body_two = [html.Tbody(elem_input_array)]
            return [dbc.Row([
                        dbc.Table(
                            html.Tbody([
                                html.Tr([
                                    html.Td(
                                        dbc.Table(table_body_one, bordered=True),
                                    ),
                                    html.Td(
                                        dbc.Table(table_body_two, bordered=True),
                                    )
                                ]),
                            ])
                        )
                    ])
                ]
        
        
        @app.expanded_callback(
            Output('table-editing-simple-output', 'figure'),
            [Input('checked-rows', 'children'),
             Input('spectrum_id', 'value'),
             Input('min-flux', 'value'),
             Input('max-flux', 'value'),
             Input('bin-factor', 'value'),
             Input('spectra-compare-dropdown', 'value'),
             Input('mask-lines-checklist', 'value'),
             State('table-editing-simple-output', 'figure')])
        def display_output(selected_rows,
                           #selected_row_ids, columns, 
                           value, min_flux, max_flux, bin_factor, compare_target, mask_value, fig_data, *args, **kwargs):
            
            spectrum_id = value
            graph_data = {'data': fig_data['data'],#[],
                          'layout': fig_data['layout']}
        
            if compare_target:
                compared = False
                # Check if comparison spectra are already plotted
                for d in reversed(graph_data['data']):
                    if '---' in d['name']:
                        compared = True
                        break
                
                if not compared:
                    # Plot this spectrum and the spectrum for the selected target, normalized to the median
                    graph_data['data'] = []
                    
                    min_flux = 0
                    max_flux = 0
        
                    spectrum = ReducedDatum.objects.get(id=spectrum_id)
               
                    object_z_query = TargetExtra.objects.filter(target_id=spectrum.target_id,key='redshift').first()
                    if not object_z_query:
                        object_z = 0
                    else:
                        object_z = float(object_z_query.value)
        
                    if not spectrum:
                        return 'No spectra yet'
                        
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
                            
                    median_flux = [f / median(flux) for f in flux]
                    if max(median_flux) > max_flux: max_flux = max(median_flux)
        
                    if not bin_factor:
                        bin_factor = 1
                    binned_wavelength, binned_flux = bin_spectra(wavelength, median_flux, int(bin_factor))
                    
                    scatter_obj = go.Scatter(
                        x=binned_wavelength,
                        y=binned_flux,
                        name='This Target',
                        line_color='black'
                    )
                    graph_data['data'] = [scatter_obj]
        
                    target = Target.objects.filter(Q(name__icontains=compare_target) | Q(aliases__name__icontains=compare_target)).first()
                    
                    compare_z_query = TargetExtra.objects.filter(target_id=target.id,key='redshift').first()
                    if not compare_z_query:
                        compare_z = 0
                    else:
                        compare_z = float(compare_z_query.value)
        
                    spectral_dataproducts = ReducedDatum.objects.filter(target=target, data_type='spectroscopy').order_by('-timestamp')
                    for spectrum in spectral_dataproducts:
                        datum = spectrum.value
                        wavelength = []
                        flux = []
                        name = target.name + ' --- ' +  str(spectrum.timestamp).split(' ')[0]
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
                        shifted_wavelength = [w * (1+object_z) / (1+compare_z) for w in wavelength]
                        median_flux = [f / median(flux) for f in flux]
                        if max(median_flux) > max_flux: max_flux = max(median_flux)
                        
                        if not bin_factor:
                            bin_factor = 1
                        binned_wavelength, binned_flux = bin_spectra(shifted_wavelength, median_flux, int(bin_factor))
                        
                        scatter_obj = go.Scatter(
                            x=binned_wavelength,
                            y=binned_flux,
                            name=name
                        )
                        graph_data['data'].append(scatter_obj)
                
                    graph_data['layout']['xaxis']['range'] = [min(binned_wavelength), max(binned_wavelength)]
                    graph_data['layout']['xaxis']['autorange'] = False
                    graph_data['layout']['yaxis']['range'] = [min_flux, max_flux]
                    graph_data['layout']['yaxis']['autorange'] = False
                    return graph_data
                
            # Remove all the element lines so we can replot the selected ones later
            for d in reversed(graph_data['data']):
                if d['name'] in self.elements.keys() or d['name'] in self.custom_elements.keys():
                    graph_data['data'].remove(d)
            
            # If the page just loaded, plot all the spectra
            if not graph_data['data']:
                logger.info('Plotting dash spectrum for dataproduct %s', spectrum_id)
                spectrum = ReducedDatum.objects.get(id=spectrum_id)
         
                if not spectrum:
                    return 'No spectra yet'
                    
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
                
                if not bin_factor:
                    bin_factor = 1
                binned_wavelength, binned_flux = bin_spectra(wavelength, flux, int(bin_factor))
                scatter_obj = go.Scatter(
                    x=binned_wavelength,
                    y=binned_flux,
                    name=name,
                    line_color='black'
                )
                graph_data['data'].append(scatter_obj)
                return graph_data
        
            if not compare_target:
                # Replot the spectrum with correct binning
                for d in reversed(graph_data['data']):
                    if d['name'] not in self.elements.keys():
                        graph_data['data'].remove(d)
        
                spectrum = ReducedDatum.objects.get(id=spectrum_id)
        
                if not spectrum:
                    return 'No spectra yet'
        
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
        
                if 'mask' in mask_value:
                    object_z_query = TargetExtra.objects.filter(target_id=spectrum.target_id,key='redshift').first()
                    if not object_z_query:
                        object_z = 0
                    else:
                        object_z = float(object_z_query.value)
        
                    pfit = np.poly1d(np.polyfit(wavelength, flux, 4))
                    for galaxy_wave in self.elements['Galaxy']['waves']:
                        mask = [abs(l-galaxy_wave*(1+object_z)) < 10 for l in wavelength]
                        flux = np.ma.masked_array(flux, mask)
                        median_flux = np.ma.median(np.ma.masked_array(pfit(wavelength), np.logical_not(mask)))
                        flux = flux.filled(fill_value=median_flux)
                    name += ' (galaxy lines masked)'
        
                if not bin_factor:
                    bin_factor = 1
                binned_wavelength, binned_flux = bin_spectra(wavelength, flux, int(bin_factor))
                scatter_obj = go.Scatter(
                    x=binned_wavelength,
                    y=binned_flux,
                    name=name,
                    line_color='black'
                )
                graph_data['data'].append(scatter_obj)
                graph_data['layout']['xaxis']['range'] = [min(binned_wavelength), max(binned_wavelength)]
                graph_data['layout']['xaxis']['autorange'] = False
                graph_data['layout']['yaxis']['range'] = [min(binned_flux), max(binned_flux)]
                graph_data['layout']['yaxis']['autorange'] = False
            
            for row in selected_rows:
                (elem, row_extras), = json.loads(row).items()
                z = row_extras['redshift']
                if not z:
                    z = 0
                v = row_extras['velocity']
                if not v:
                    v = 0
                try:
                    v_over_c = float(v/(3e5))
                except:
                    v_over_c = 0
                lambda_rest = row_extras['waves']
                if not lambda_rest[0]:
                    continue
                x = []
                y = []
                
                if compare_target: # We need to get the min and max fluxes for the element lines
                    max_flux = max([max(d['y']) for d in graph_data['data'] if d['name'] not in self.elements.keys() and d['name'] not in self.custom_elements.keys()])
                    min_flux = min([min(d['y']) for d in graph_data['data'] if d['name'] not in self.elements.keys() and d['name'] not in self.custom_elements.keys()])
                for lambduh in lambda_rest:
        
                    lambda_observed = lambduh*((1+z)-v_over_c)
            
                    x.append(lambda_observed)
                    x.append(lambda_observed)
                    x.append(None)
                    y.append(min_flux)
                    y.append(max_flux)
                    y.append(None)
        
                try:
                    color = row_extras['color']
                except:
                    color = 'black'
        
                graph_data['data'].append(
                    go.Scatter(
                        x=x,
                        y=y,
                        name=elem,
                        mode='lines',
                        line={'color': color},
                    )
                )
            return graph_data


spec_individual = SpectraIndividual(app=app)
