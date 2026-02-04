from typing import ClassVar

import scipy

from .bls_data_visualizer import BlsDataVisualizer
from brimview_widgets.bls_types import bls_param
import panel as pn
from panel.io import hold
import param
import holoviews as hv
from holoviews import streams

from holoviews.selection import link_selections
from matplotlib.path import Path

import numpy as np
import xarray as xr

from .logging import logger

import brimfile as bls
from .bls_file_input import BlsFileInput
from .utils import only_on_change, catch_and_notify
from .widgets import HorizontalEditableIntSlider
import colorcet as cc
import pandas as pd

import sys

# DEBUG
import time
import datetime as dt

from panel.widgets.base import WidgetBase
from panel.custom import PyComponent


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

        # Because we're not a pn.Viewer anymore, by default we lost the "card" display
        # so despite us returning a card from __panel__, the shown card didn't match
        # the card display (background color, shadows)
        self.css_classes.append("card")

        # Typing hints
        self.bls_data: bls_param
        self.img_mask: xr.DataArray
        self.selected_points: list[tuple[int, int, int]]

    @pn.depends("img_mask", watch=True)
    def _mask_updated(self):
        print("Statistics widget: mask updated")
        # Here we would compute statistics based on the updated mask

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
        if not self.selected_points:
            return pn.pane.Markdown("No points selected.")
        else:
            df = pd.DataFrame(self.selected_points, columns=["z", "y", "x"])
            return pn.widgets.DataFrame(df, autosize_mode="fit_columns", height=200)

    @pn.depends("selected_points")
    # @catch_and_notify(prefix="<b>Compute average spectrum: </b>")
    def average_spectrum_widget(self):
        if not self.selected_points or self.bls_data is None:
            logger.debug(
                "No points selected or no BLS data loaded for average spectrum"
            )
            return pn.pane.Markdown("No points selected or no BLS data loaded.")
        else:
            # Retrieve spectra for selected points
            retrieved_PSD = []
            retrieved_frequency = []
            for z, y, x in self.selected_points:
                PSD, frequency, PSD_units, frequency_units = (
                    self.bls_data.data.get_spectrum_in_image((z, y, x))
                )
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
            for i in range(len(self.selected_points)):
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
            return pn.pane.HoloViews(plot)

    def __panel__(self):
        """Create Panel layout for the statistics widget."""
        card = pn.Card(
            pn.pane.Markdown(
                "## Statistics\n\nStatistics of selected regions will be displayed here."
            ),
            self.average_spectrum_widget,
            self.mask_status,
            self.selected_points_widget,
            title="BLS Statistics",
            sizing_mode="stretch_height",
        )
        return card
