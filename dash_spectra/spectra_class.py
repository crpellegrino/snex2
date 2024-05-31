import dash
from dash.dependencies import Input, Output, State
import dash_table
import dash_bootstrap_components as dbc
import dash_core_components as dcc
import dash_html_components as html
import plotly.graph_objs as go
import numpy as np
import json

from django_plotly_dash import DjangoDash
from django.conf import settings
from tom_dataproducts.models import ReducedDatum
from .util import bin_spectra
from django.templatetags.static import static

external_stylesheets = [dbc.themes.BOOTSTRAP]
app = DjangoDash(name='Spectra_Class', id='target_id', add_bootstrap_links=True, suppress_callback_exceptions=True)
app.css.append_css({'external_url': static('custom_code/css/dash.css')})


class Spectra:

    params = [
        'Redshift', 'Velocity (km/s)'
    ]

    elements = settings.DASH_SPECTRA_PLOTS['elements']

    custom_elements = settings.DASH_SPECTRA_PLOTS['custom_lines']

    def __init__(self, app=None):
        self.app = app
        self.callbacks(self.app)

        #TODO: Integrate tooltips into hover over elements
        #self.tooltips = [{
        #    'value': 'rest wavelengths: ' + str(self.elements[elem]['waves']),
        #    'type': 'text',
        #    'if': {'column_id': 'Element', 'row_index': list(self.elements).index(elem)}
        #} for elem in self.elements]

        self.layout = self.make_layout()

        app.layout = self.layout


    def make_layout(self):
        columns = [{'id': p, 'name': p} for p in self.params]
        columns.append({'id': 'Element', 'name': 'Element', 'editable': False})
        columns.insert(0, columns.pop())

        table_body_one = [html.Tbody([])]
        table_body_two = [html.Tbody([])]

        return html.Div([
            dcc.Graph(id='table-editing-simple-output',
                      figure = {'layout' : {'height': 350,
                                            'margin': {'l': 60, 'b': 30, 'r': 60, 't': 10},
                                            'yaxis': {'type': 'linear', 'tickformat': '.1e'},
                                            'xaxis': {'showgrid': False},
                                            'legend': {'x': 0.85, 'y': 1.0},
                                            },
                                'data' : []
                            }
            ),
            html.Div([
                dcc.Input(id='target_id', type='hidden', value=0),
                dcc.Input(id='target_redshift', type='hidden', value=0),
                dcc.Input(id='min-flux', type='hidden', value=0),
                dcc.Input(id='max-flux', type='hidden', value=0),
                dcc.Checklist(
                    id='line-plotting-checklist',
                    options=[{'label': 'Show line plotting interface', 'value': 'display'}],
                    value=''
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
                )
            ], style={'overflow-y': 'auto'})
        ])


    def callbacks(self, app):
        @app.callback(
            Output('table-container-div', 'style'),
            [Input('line-plotting-checklist', 'value')])
        def show_table(value, *args, **kwargs):
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
             Input('target_id', 'value'),
             Input('min-flux', 'value'),
             Input('max-flux', 'value'),
             State('table-editing-simple-output', 'figure')])
        def display_output(selected_rows,
                           value, min_flux, max_flux, fig_data, *args, **kwargs):
            
            target_id = value
            if fig_data:
                graph_data = {'data': fig_data['data'],
                              'layout': fig_data['layout']}
            else:
                graph_data = {'data': [],
                              'layout': []}
        
            # If the page just loaded, plot all the spectra
            if not fig_data['data']:
                spectral_dataproducts = ReducedDatum.objects.filter(target_id=target_id, data_type='spectroscopy').order_by('timestamp')
                if not spectral_dataproducts:
                    return 'No spectra yet'
                rgb_colors = go.colors.sample_colorscale('rainbow_r', len(spectral_dataproducts))
                all_data = []
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
                            wavelength.append(float(value['wavelength']))
                            flux.append(float(value['flux']))
                    
                    binned_wavelength, binned_flux = bin_spectra(wavelength, flux, 5)
                    scatter_obj = go.Scatter(
                        x=binned_wavelength,
                        y=binned_flux,
                        name=name,
                        line_color=rgb_colors[i]
                    )
                    graph_data['data'].append(scatter_obj)

                return graph_data
        
            for d in reversed(fig_data['data']):
                if d['name'] in self.elements.keys() or d['name'] in self.custom_elements.keys():
                    # Remove all the element lines that were plotted last time
                    fig_data['data'].remove(d)
            
            for row in selected_rows:
                for elem, row_extras in json.loads(row).items():
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
                x = []
                y = []
                
                lambda_rest = row_extras['waves']
                for lambduh in lambda_rest:
        
                    lambda_observed = lambduh*((1+z)-v_over_c)
            
                    x.append(lambda_observed)
                    x.append(lambda_observed)
                    x.append(None)
                    y.append(min_flux*0.95)
                    y.append(max_flux*1.05)
                    y.append(None)
        
                graph_data['data'].append(
                    go.Scatter(
                        x=x,
                        y=y,
                        name=elem,
                        mode='lines',
                        line=dict(color=row_extras['color'])
                    )
                )
            return graph_data


spec = Spectra(app=app)
