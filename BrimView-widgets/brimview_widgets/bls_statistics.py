from .bls_data_visualizer import BlsDataVisualizer
from .logging import logger
from .bls_types import bls_param

import panel as pn
from panel.widgets.base import WidgetBase
from panel.custom import PyComponent
import param
import holoviews as hv
import numpy as np
import scipy
import xarray as xr
import pandas as pd


ZYXPoints = list[tuple[int, int, int]]


class BlsStatistics(WidgetBase, PyComponent):
    """
    Widget to compute and display basic statistics of selected regions in BLS data.
    """

    # === References to external objects ===
    bls_data = param.ClassSelector(
        class_=bls_param,
        default=None,
        precedence=-1,
        doc="The current selected BLS file/data",
        allow_refs=True,
    )

    img_mask = param.Parameter(
        default=None,
        precedence=-1,
        doc="Selection mask from the data visualizer",
        allow_refs=True,
    )

    # Information to go from displayed mask to bls data coordinates
    img_axis_1 = param.Selector(
        default="x", objects=["x", "y", "z"], label="Horizontal axis", allow_refs=True
    )
    img_axis_2 = param.Selector(
        default="y", objects=["x", "y", "z"], label="Vertical axis", allow_refs=True
    )
    img_axis_3 = param.Selector(default="z", objects=["x", "y", "z"], allow_refs=True)
    img_axis_3_slice = param.Integer(
        default=0, label="3rd axis slice selector", allow_refs=True
    )

    # === Internal state ===
    selected_points = param.List(
        default=[],
        precedence=-1,
        doc="List of selected points (z, y, x ) in data coordinates",
    )

    def __init__(self, result_plot: BlsDataVisualizer, **params):
        params["name"] = "Group Statistics"
        super().__init__(**params)

        # === Linking to other widgets ===
        # TODO: update result_plot to use this new class
        self.bls_data: bls_param = bls_param(
            file=result_plot.param.bls_file,
            data=result_plot.param.bls_data,
            analysis=result_plot.param.bls_analysis,
        )

        self.img_mask = result_plot.param.mask
        self.img_axis_1 = result_plot.param.img_axis_1
        self.img_axis_2 = result_plot.param.img_axis_2
        self.img_axis_3 = result_plot.param.img_axis_3
        self.img_axis_3_slice = result_plot.param.img_axis_3_slice

        # === Some panel setup ===
        # Because we're not a pn.Viewer anymore, by default we lost the "card" display
        # so despite us returning a card from __panel__, the shown card didn't match
        # the card display (background color, shadows)
        self.css_classes.append("card")

        self.spectrum_plot_widget = pn.pane.HoloViews(None)
        self.statistic_tabulator_widget = self.statistic_tabulator()

        self.tqdm = pn.widgets.Tqdm(visible=False)

        # === Typing hints ===
        self.bls_data: bls_param
        self.img_mask: xr.DataArray
        self.selected_points: list[tuple[int, int, int]]

    @pn.depends("img_mask")
    def mask_status(self):
        if self.img_mask is None:
            return "No selection"
        else:
            num_selected = np.count_nonzero(self.img_mask)
            total_pixels = self.img_mask.size
            pct_selected = 100 * num_selected / total_pixels
            return f"Selected pixels: {num_selected} ({pct_selected:.2f}%)"

    @pn.depends("img_mask", watch=True)
    def update_selected_points(self):
        """Update the list of selected points based on the current image mask."""
        if self.img_mask is None:
            self.selected_points = []
        else:
            self.selected_points = self.mask_to_list(self.img_mask)

    def mask_to_list(self, mask: xr.DataArray) -> list[tuple[int, int, int]]:
        """Convert a 2D mask DataArray to a list of selected points (z, y, x)."""
        selected_points = []
        mask_indices = np.argwhere(mask.values)
        for idx in mask_indices:
            y_displayed, x_displayed = idx
            # Assuming single z slice for simplicity; extend as needed

            # TODO: Maybe it's time now to create a proper coordinate mapper class?
            match self.img_axis_1:
                case "x":
                    x = x_displayed
                case "y":
                    y = x_displayed
                case "z":
                    z = x_displayed
            match self.img_axis_2:
                case "x":
                    x = y_displayed
                case "y":
                    y = y_displayed
                case "z":
                    z = y_displayed
            match self.img_axis_3:
                case "x":
                    x = self.img_axis_3_slice
                case "y":
                    y = self.img_axis_3_slice
                case "z":
                    z = self.img_axis_3_slice

            selected_points.append((z, y, x))
        return selected_points

    @pn.depends("selected_points")
    def selected_points_widget(self):
        """
        Display the list of selected points in a DataFrame.
        Should only be used for debugging due to potential performance issues.
        """
        if not self.selected_points:
            return pn.pane.Markdown("No points selected.")
        else:
            df = pd.DataFrame(self.selected_points, columns=["z", "y", "x"])
            return pn.widgets.DataFrame(df, autosize_mode="fit_columns", height=200)

    # === Average spectrum computation and visualization ===

    def fetch_data_from_points(self, selected_points: ZYXPoints):
        """
        Fetch data from the selected points.
        """
        if not selected_points or self.bls_data is None:
            logger.debug(
                "No points selected or no BLS data loaded for average spectrum"
            )
            return (
                None,
                None,
            )
        else:
            all_spectra = []
            all_quantities = []
            for z, y, x in self.tqdm(
                selected_points, desc="Fetching data from points", leave=False
            ):
                spectrum, quantities = (
                    self.bls_data.data.get_spectrum_and_all_quantities_in_image(
                        ar=self.bls_data.analysis, coor=(z, y, x)
                    )
                )
                all_spectra.append(spectrum)
                all_quantities.append(quantities)
            return all_spectra, all_quantities

    @pn.depends("selected_points", watch=True)
    async def update_widget(self):
        if self.bls_data is None or not self.selected_points:
            logger.debug("No data or no points selected, skipping statistics update")
            self.spectrum_plot_widget.object = None
            self.statistic_tabulator_widget.value = pd.DataFrame()
            return
        
        self.tqdm.visible = True
        spectra, quantities = self.fetch_data_from_points(self.selected_points)

        # spectra: (PSD, frequency, PSD_units, frequency_units)
        (common_freq, mean_spectrum, std_spectrum, PSD_units, frequency_units) = (
            self.compute_average_spectrum(spectra)
        )
        curve = self.plot_average_spectrum(
            common_freq, mean_spectrum, std_spectrum, PSD_units, frequency_units
        )
        self.spectrum_plot_widget.object = curve

        # quantities: result[quantity.name][peak.name] = bls.Metadata.Item(value, units)
        df_quantities = self.compute_average_quantities(quantities)
        self.statistic_tabulator_widget.value = df_quantities
        self.tqdm.visible = False

    def compute_average_spectrum(
        self, spectra
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, str, str]:
        retrieved_PSD = []
        retrieved_frequency = []
        for PSD, frequency, PSD_units, frequency_units in spectra:
            retrieved_PSD.append(PSD)
            retrieved_frequency.append(frequency)

        # Generate average PSD
        # Figuring out which frequency axis to use
        n_data_points = len(
            retrieved_PSD[0]
        )  # Assuming they all have the same number of points
        freq_min = np.nanmin(retrieved_frequency)
        freq_max = np.nanmax(retrieved_frequency)
        common_freq = np.linspace(freq_min, freq_max, n_data_points)  # shape (71,)

        # Interpolating all the PSDs to the common frequency axis
        interpolated_psd = np.empty((len(self.selected_points), len(common_freq)))
        for i in self.tqdm(
            range(len(self.selected_points)), desc="Interpolating spectra", leave=False
        ):
            interp_func = scipy.interpolate.interp1d(
                retrieved_frequency[i],
                retrieved_PSD[i],
                kind="linear",
                bounds_error=False,
                fill_value="extrapolate",
            )
            interpolated_psd[i, :] = interp_func(common_freq)

        mean_spectrum = np.mean(interpolated_psd, axis=0)  # shape (71,)
        std_spectrum = np.nanstd(interpolated_psd, axis=0)
        return (common_freq, mean_spectrum, std_spectrum, PSD_units, frequency_units)

    def compute_average_quantities(self, quantities) -> pd.DataFrame:
        """

        Assuming quantities: result[quantity.name][peak.name] = bls.Metadata.Item(value, units)
        """
        df_rows = []
        for quantity_name in self.tqdm(
            quantities[0].keys(), desc="Averaging quantities", leave=False
        ):
            for peak_name in self.tqdm(
                quantities[0][quantity_name].keys(),
                desc=f"Averaging peaks for {quantity_name}",
                leave=False,
            ):
                values = [
                    quantities[i][quantity_name][peak_name].value
                    for i in range(len(quantities))
                ]
                mean_value = np.mean(values)
                std_value = np.std(values)
                logger.debug(
                    f"Average {quantity_name} ({peak_name}): {mean_value:.3f} ± {std_value:.3f}"
                )
                new_row = {
                    "Peak": peak_name,
                    "Quantity": quantity_name,
                    "Mean": mean_value,
                    "Std": std_value,
                    "Units": quantities[0][quantity_name][peak_name].units,
                }
                df_rows.append(new_row)
        df = pd.DataFrame(df_rows)
        return df

    # === Panel display method / GUI logic===

    def statistic_tabulator(self) -> pn.widgets.Tabulator:
        tab = pn.widgets.Tabulator(
            pd.DataFrame(
                {
                    "Peak": ["Peak placeholder"],
                    "Quantity": ["Quantity placeholder"],
                    "Mean": [0.0],
                    "Std": [1.0],
                    "Units": ["Units placeholder"],
                }
            ),
            show_index=False,
            disabled=True,
            groupby=["Peak"],
            hidden_columns=["Peak", "Description"],
            configuration={
                "groupStartOpen": True  # This makes all groups collapsed initially
            },
        )
        return tab

    def plot_average_spectrum(
        self, common_freq, mean_spectrum, std_spectrum, PSD_units, frequency_units
    ) -> hv.Curve:
        curve = hv.Curve(
            (common_freq, mean_spectrum),
            hv.Dimension("Frequency", unit=frequency_units),
            hv.Dimension("PSD", unit=PSD_units),
            label=f"Average Spectra",
        ).opts(
            tools=["hover"],
        )
        spread = hv.Spread((common_freq, mean_spectrum, std_spectrum))
        plot = curve * spread
        return plot

    def __panel__(self):
        """Create Panel layout for the statistics widget."""
        card = pn.Card(
            pn.pane.Markdown(
                "## Statistics\n\nStatistics of selected regions will be displayed here."
            ),
            self.tqdm,
            self.spectrum_plot_widget,
            self.statistic_tabulator_widget,
            self.mask_status,
            title="BLS Statistics",
            sizing_mode="stretch_height",
        )
        return card
