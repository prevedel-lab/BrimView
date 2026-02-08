from typing import ClassVar
import panel as pn
from panel.io import hold
import param
import holoviews as hv
from holoviews import streams

from .utils import points_in_polygon
try:
    import scipy

    _GUI_ROI_SELECTION = True
except ImportError:
    _GUI_ROI_SELECTION = False


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


def get_linear_colormaps() -> dict:
    """
    Creates the dictionnary of of colorpalettes to be displayed in the app.
    Returns { cmap_human_name : cmap }
    """
    cmap_list = cc.all_original_names(only_aliased=True, not_group="glasbey")
    cmap_name_list = [cc.get_aliases(cmap).split(", ")[0] for cmap in cmap_list]
    cmap_dict = {cmap_name: cc.palette_n[cmap_name] for cmap_name in cmap_name_list}
    # logger.debug(cmap_dict)
    return cmap_dict


class BlsDataVisualizer(WidgetBase, PyComponent):
    """
    Class to display a single data group from the HDF5 file.

    bls_data
        -> result_index
        ->
    """

    result_index = param.ObjectSelector(
        default="Treatment 1", objects=["Treatment 1", "Treatment 2", "Treatment 3"]
    )
    result_quantity = param.ObjectSelector(
        default="Shift", objects=["Shift", "Linewidth", "Offset"]
    )

    result_peak = param.ObjectSelector(
        default=bls.Data.AnalysisResults.PeakType.average,
        objects=[bls.Data.AnalysisResults.PeakType.average],
    )

    colormap = param.ObjectSelector(default=cc.palette["fire"], objects=cc.palette)
    colorrange = param.Range(default=(0, 1), bounds=None)
    # a parameter controlling whether the autoscale for the color range should be enabled
    autoscale = param.Boolean(default=True)

    # === **Internal Param**
    #   we need then to pass some signals, but we don't want them to
    #   be diplayed on the UI. Puting precedence=-1 seems to do the trick
    # ===
    # This allows to have Param triggers, to automatically call the correct functions
    bls_data = param.ClassSelector(class_=bls.Data, default=None, allow_refs=True)
    bls_file = param.ClassSelector(
        class_=bls.File, default=None, allow_refs=True
    )  # usefull to keep the reference, in case we want to get some metadata

    bls_analysis = param.ClassSelector(
        class_=bls.Data.AnalysisResults, default=None, precedence=-1
    )

    # The numpy array to be displayed
    img_data = param.Array(default=None, instantiate=False, precedence=-1)
    mask = param.Parameter(default=None, instantiate=False, precedence=-1)

    img_axis_1 = param.Selector(
        default="x", objects=["x", "y", "z"], label="Horizontal axis"
    )
    img_axis_2 = param.Selector(
        default="y", objects=["x", "y", "z"], label="Vertical axis"
    )
    img_axis_3 = param.Selector(default="z", objects=["x", "y", "z"])
    img_axis_3_slice = param.Integer(default=0, label="3rd axis slice selector")
    slices = param.List(default=[0], precedence=-1)
    img_dataset = param.ClassSelector(
        class_=hv.Dataset,
        default=hv.Dataset(
            xr.DataArray(
                np.zeros((1, 250, 250)),  # shape: (z, y, x)
                dims=["z", "y", "x"],
                coords={
                    "x": range(250),
                    "y": range(250),
                    "z": [0],  # 1-length z
                },
                name="value",
            )
        ),
    )
    img_vunit = param.ClassSelector(
        class_=hv.Dimension, default=hv.Dimension("default"), precedence=-1
    )

    # 1px -> physical value conversion
    use_physical_units = param.Boolean(
        default=True,
        label="Use physical units",
        doc="If false, uses pixel indexing. If right, converts the pixel index into the proper physical units",
    )
    x_px = param.ClassSelector(
        class_=bls.Metadata.Item, default=bls.Metadata.Item(1, "px")
    )
    y_px = param.ClassSelector(
        class_=bls.Metadata.Item, default=bls.Metadata.Item(1, "px")
    )
    z_px = param.ClassSelector(
        class_=bls.Metadata.Item, default=bls.Metadata.Item(1, "px")
    )

    # Records where the user clicked on the (main) plot
    # + as a param, allows other function to react to that
    # plot_clicks = param.NumericTuple(length=3, instantiate=False)
    _dataset_zyx_click = param.NumericTuple(default=(0, 0, 0))
    dataset_zyx_click = param.NumericTuple(default=(0, 0, 0))

    def __init__(self, Bh5file: BlsFileInput, **params):

        self.spinner = pn.indicators.LoadingSpinner(
            value=False, size=20, name="Idle", visible=True
        )

        # Bh5file.param.watch(self._update_data, ["data"])
        # self.get_bh5_file = Bh5file.get_bh5_file
        self.img_data = np.zeros((512, 512))  # Placeholder for no data
        self.index = -1

        # self.img_dataset = hv.Dataset(
        #     (range(512), range(512), range(1), np.zeros((1, 512, 512))),
        #     ['x', 'y', 'z'], "value"
        # )
        logger.debug(f"Dataset from init {self.img_dataset}")
        self.plot = hv.Image([])
        self.histogram = hv.Histogram([])

        params["name"] = "Data Analysis visualization"
        super().__init__(**params)

        # Explicit annotation, because param and type hinting is not working properly
        self.bls_data: bls.Data = Bh5file.param.data
        self.bls_file: bls.File = Bh5file.param.bls_file

        # Because we're not a pn.Viewer anymore, by default we lost the "card" display
        # so despite us returning a card from __panel__, the shown card didn't match
        # the card display (background color, shadows)
        self.css_classes.append("card")

    @pn.depends("loading", watch=True)
    def loading_spinner(self):
        """
        Controls an additional spinner UI.
        This goes on top of the `loading` param that comes with panel widgets.

        This is especially usefull in the `panel convert` case,
        because some UI elements can't updated easily (or at least in the same way as `panel serve`).
        In particular, the visible toggle is not always working, and elements inside Rows and Columns sometimes
        don't get updated.
        """
        with param.parameterized.batch_call_watchers(self.spinner):
            if self.loading:
                self.spinner.value = True
                self.spinner.name = "Loading..."
                self.spinner.visible = True
            else:
                self.spinner.value = False
                self.spinner.name = "Idle"
                self.spinner.visible = True

    def rewrite_card_header(self, card: pn.Card):
        """
        Changes a bit how the header of the card is displayed.
        We replace the default title by
            [{self.name}     {spinner}]

        With self.name to the left and spinner to the right
        """
        params = {
            "object": f"<h3>{self.name}</h3>" if self.name else "&#8203;",
            "css_classes": card.title_css_classes,
            "margin": (5, 0),
        }
        self.spinner.align = ("end", "center")
        self.spinner.margin = (10, 30)
        header = pn.FlexBox(
            pn.pane.HTML(**params),
            # self.spinner,
            # pn.Spacer(),  # pushes next item to the right
            self.spinner,
            align_content="space-between",
            align_items="center",  # Vertical-ish
            sizing_mode="stretch_width",
            justify_content="space-between",
        )
        # header.styles = {"place-content": "space-between"}
        card.header = header
        card._header_layout.styles = {"width": "inherit"}

    @param.depends("bls_data", watch=True)
    @catch_and_notify(prefix="<b>File loading: </b>")
    def _read_bls_data(self):
        """
        This function is called when the bls_data is changed.

        It will manually call the correct function to update everything.
        Some caching mechanism at the function levels will help to not recompute everything unnecessarily.
        """
        self.loading = True

        with param.parameterized.batch_call_watchers(self):
            self._update_result_list()  # Read the list of available results
            self._update_result_variable()  # Read the list of available quantities and peaks
            self._update_img_data()  # Read the actual data

            self._autoscale_colorrange()  # Update the colorrange to the new data
            self._update_axis_3()  # Update the 3rd axis slice to the new data
            self._compute_histogram()

        self.loading = False

    @catch_and_notify(prefix="<b>Update results: </b>")
    def _update_result_list(self):
        if self.bls_data is None:
            self.result_index_dropdown.disabled = True
            return

        # Update Analysis
        results_list = self.bls_data.list_AnalysisResults(retrieve_custom_name=True)
        cleaned_results_list = {
            result["custom_name"]: result["index"] for result in results_list
        }
        self.param.result_index.objects = cleaned_results_list
        self.result_index = list(cleaned_results_list.values())[0]
        if len(cleaned_results_list) > 1:
            self.result_index_dropdown.disabled = False
        else:
            self.result_index_dropdown.disabled = True

    @param.depends("result_index", watch=True)  # User IO
    @only_on_change("bls_data", "result_index")
    @catch_and_notify(prefix="<b>Update results: </b>")
    def _update_result_variable(self):
        if self.bls_data is None or self.result_index is None:
            return

        # Synchronously update the param variables
        with param.parameterized.batch_call_watchers(self):
            self.bls_analysis = self.bls_data.get_analysis_results(self.result_index)

            # Placeholder until a list_AnalysisQuantites or similar exist
            quantity_list = self.bls_analysis.list_existing_quantities()

            # Update peak types
            peak_list = list(self.bls_analysis.list_existing_peak_types())
            if len(peak_list) >= 2:
                # We can only do an average, if we have 2 peaks
                peak_list.insert(0, bls.Data.AnalysisResults.PeakType.average)

            self.param.result_peak.objects = peak_list
            self.result_peak = peak_list[0]
            if len(peak_list) > 1:
                self.result_peak_dropdown.disabled = False
            else:
                self.result_peak_dropdown.disabled = True

            self.param.result_quantity.objects = quantity_list
            self.result_quantity = quantity_list[0]
            if len(quantity_list) > 1:
                self.result_quantity_dropdown.disabled = False
            else:
                self.result_index_dropdown.disabled = True

    @param.depends(
        "result_quantity",  # User IO
        "result_peak",  # User IO
        "use_physical_units",  # User IO
        watch=True,
    )
    @only_on_change(
        "bls_analysis", "result_quantity", "result_peak", "use_physical_units"
    )
    @catch_and_notify(prefix="<b>Update image: </b>")
    def _update_img_data(self):
        if self.bls_analysis is None:
            self.img_data = np.zeros((512, 512))
            return

        (img_data, px_units) = self.bls_analysis.get_image(
            self.result_quantity, self.result_peak
        )
        # img_data = img_data[1, :, :]
        # TODO remove this hack once we have updated brimfile
        target_peak = self.result_peak
        if target_peak == bls.Data.AnalysisResults.PeakType.average:
            target_peak = self.bls_analysis.list_existing_peak_types()[0]

        self.img_vunit = hv.Dimension(
            self.result_quantity.name,
            unit=self.bls_analysis.get_units(self.result_quantity, target_peak),
        )

        if isinstance(px_units[0], float):
            px_units = (
                bls.Metadata.Item(px_units[0], "Unknown"),
                bls.Metadata.Item(px_units[1], "Unknown"),
                bls.Metadata.Item(px_units[2], "Unknown"),
            )
        # Converting into a holoview Dataset, with the correct dimension and metadata
        px_units: tuple[bls.Metadata.Item, bls.Metadata.Item, bls.Metadata.Item] = (
            px_units
        )
        if self.use_physical_units:
            (self.z_px, self.y_px, self.x_px) = px_units
            # There seems to be a bug in the current BLS software, so placeholder
            # self.x_px.units = "um"
            # self.y_px.units = "um"
            # self.z_px.units = "um"
        else:
            (self.z_px, self.y_px, self.x_px) = (
                bls.Metadata.Item(1, "px"),
                bls.Metadata.Item(1, "px"),
                bls.Metadata.Item(1, "px"),
            )
        logger.debug(img_data.shape)
        (z, y, x) = img_data.shape
        logger.debug(self.z_px)
        logger.debug(self.y_px)
        logger.debug(self.x_px)

        # We want a Xarray backed dataset
        xr_data = xr.DataArray(
            img_data,
            dims=["z", "y", "x"],
            coords={
                "x": np.arange(x) * self.x_px.value,
                "y": np.arange(y) * self.y_px.value,
                "z": np.arange(z) * self.z_px.value,
            },
            name="value",
        )

        # We add the correct units in the hv.Dataset metadata
        self.img_dataset = hv.Dataset(xr_data).redim(
            value=self.img_vunit,
            x=hv.Dimension("x", label="x", unit=self.x_px.units),
            y=hv.Dimension("y", unit=self.y_px.units),
            z=hv.Dimension("z", unit=self.z_px.units),
        )

        # self.img_data = img_data[1, :, :]

    @param.depends("_update_img_data")
    def phys_unit_widget(self):
        return pd.DataFrame(
            index=["x", "y", "z"],
            data={
                "physical size": [self.x_px.value, self.y_px.value, self.z_px.value],
                "unit": [self.x_px.units, self.y_px.units, self.z_px.units],
            },
        )

    @param.depends("img_axis_1", watch=True)
    def _update_axis_1(self):
        dims = ["x", "y", "z"]
        # Make sure axis_1 and axis_2 are different
        dims.remove(self.img_axis_1)
        if self.img_axis_1 == self.img_axis_2:
            self.img_axis_2 = dims[0]
        dims.remove(self.img_axis_2)
        self.img_axis_3 = dims[0]

    @param.depends("img_axis_2", watch=True)
    def _update_axis_2(self):
        dims = ["x", "y", "z"]
        dims.remove(self.img_axis_2)
        # Make sure axis_1 and axis_2 are different
        if self.img_axis_1 == self.img_axis_2:
            self.img_axis_1 = dims[0]

        dims.remove(self.img_axis_1)
        self.img_axis_3 = dims[0]

    @param.depends("img_axis_3", "_update_img_data", watch=True)
    def _update_axis_3(self):
        self.slices = self.img_dataset.data.coords[
            self.img_axis_3
        ].values.tolist()  # This works because it's an Xarray backed dataset
        self.param.img_axis_3_slice.bounds = (
            0,
            len(self.slices) - 1,
        )
        # self.param.img_axis_3_slice.objects = options
        self.img_axis_3_slice = 0
        self.img_axis_3_slice_widget.fixed_end = len(self.slices) - 1
        self.img_axis_3_slice_widget.fixed_start = 0
        if len(self.slices) > 0:
            self.img_axis_3_slice_widget.disabled = False
        else:
            self.img_axis_3_slice_widget.disabled = True
        logger.info(
            f"Updating img_axis_3_slice with {self.slices} - value {self.img_axis_3_slice}"
        )

    def _get_datasetslice(self) -> hv.Dataset:
        # Updating the 3rd axis slices, in case we swapped between
        # index and physical units - This doesn't change the length of the list, but it's values
        self.slices = self.img_dataset.data.coords[self.img_axis_3].values.tolist()
        match self.img_axis_3:
            case "x":
                frame = self.img_dataset.select(x=self.slices[self.img_axis_3_slice])
            case "y":
                frame = self.img_dataset.select(y=self.slices[self.img_axis_3_slice])
            case "z":
                frame = self.img_dataset.select(z=self.slices[self.img_axis_3_slice])

        # Reindexing:
        # 1) puts the dimension in the 'correct' order
        # 2) flattens the dataset (3rd axis is non-varying, so it disappears from the kdims) -> we get a 2D array
        return frame.reindex(kdims=[self.img_axis_1, self.img_axis_2])

    def _img_dimension_label(self):
        if self.use_physical_units:
            match self.img_axis_3:
                case "x":
                    unit = self.x_px.units
                case "y":
                    unit = self.y_px.units
                case "z":
                    unit = self.z_px.units
        else:
            unit = "px"
        label = f"{self.img_axis_1}{self.img_axis_2}-{self.img_axis_3}:{self.slices[self.img_axis_3_slice]}{unit}"
        return label

    @(
        param.depends(
            "img_dataset",  # variable
            "_update_axis_1",  # func
            "_update_axis_2",  # func
            "_update_axis_3",  # func
            "img_axis_3_slice",  # variable
            "colormap",  # variable
            "colorrange",  # variable
            watch=False,  # This function returns something
        )
    )
    @only_on_change(
        "img_dataset",
        "img_axis_1",
        "img_axis_2",
        "img_axis_3_slice",
        "colormap",
        "colorrange",
    )
    def _plot_data(self):
        """
        When one of the parameter changes, we recreate the correct plot.

        If this appears to be to slow/expensive, we could try to replace this by some streams.pipe
        We don't really have a stream of data, so we don't really need streams.pipe.
        """
        logger.debug("_plot_data")
        frame = self._get_datasetslice()
        img = hv.Image(frame)

        # If we need to replot the data, then the mask is probably meaningless anyways
        # The argument is needed to match the signature of the PlotReset stream
        self._reset_mask(True)

        if (
            self.bls_data is None
            or self.bls_analysis is None
            or self.result_peak is None
        ):
            title = "Load data"
        else:
            # title = f"{self.bls_data.get_name()}/{self.bls_analysis.get_name()}/{self.result_peak} "
            title = f"{self.bls_data.get_name()}/{self.bls_analysis.get_name()}/{self.result_peak} ({self._img_dimension_label()})"

        if _GUI_ROI_SELECTION:
            tools = ["hover", "tap", "lasso_select"]
        else:
            tools = ["hover", "tap"]
            logger.warning("Matplotlib not found, lasso selection disabled.")

        img = img.opts(
            cmap=self.colormap,
            colorbar=True,
            clim=self.colorrange,
            clabel=f"{self.img_vunit.label} ({self.img_vunit.unit})",  # Mimics the hover tool display
            aspect="equal",
            data_aspect=1,
            axiswise=True,  # Give independent axis
            framewise=True,
            tools=tools,
            title=title,
            # padding=0.2,
            # repsonsive is not exactly working as expected, and breaks a bit the whole thing
            # See for example: https://github.com/holoviz/panel/issues/5054
            responsive=True,
        )

        # Generating the streams to record where the user clicked on the plot
        stream = streams.Tap(source=img, x=np.nan, y=np.nan)
        stream.add_subscriber(self._update_click_param)

        if _GUI_ROI_SELECTION:
            lasso = streams.Lasso(source=img)
            x_coords = self.img_dataset.data.coords[self.img_axis_1].values.tolist()
            y_coords = self.img_dataset.data.coords[self.img_axis_2].values.tolist()
            lasso.add_subscriber(
                lambda geometry: self._create_mask_from_lasso(
                    geometry, (x_coords, y_coords)
                )
            )

            reset_stream = streams.PlotReset(source=img)
            reset_stream.add_subscriber(self._reset_mask)
        return img

    def _reset_mask(self, resetting=True):
        """
        Reset the selection mask to None.

        Parameters
        ----------
        resetting : bool
            Indicates if the reset is triggered by a user action.
            Needed to match the signature of the PlotReset stream.
            Not actually used.
        """
        logger.debug("Resetting selection mask")
        self.mask = None

    def _create_mask_from_lasso(self, geometry, mask_shape):

        def lasso_to_mask(lasso_xy, coordinates_xy):
            """
            Convert a lasso polygon into a boolean pixel mask.

            Parameters
            ----------
            lasso_xy : (N, 2) array
                Polygon vertices in (x, y) data coordinates
            coordinates_xy : tuple
                (x_coords, y_coords) coordinates of the image

            Returns
            -------
            mask : (ny, nx) bool array
                True for pixels inside the lasso
            """
            nx, ny = (len(coordinates_xy[0]), len(coordinates_xy[1]))

            # Pixel centers
            xv, yv = np.meshgrid(coordinates_xy[0], coordinates_xy[1])  # yx coordinates
            points = np.column_stack([xv.ravel(), yv.ravel()])

            mask = points_in_polygon(points, lasso_xy)
            mask = mask.reshape((ny, nx))
            mask = xr.DataArray(
                mask,
                dims=[
                    "y",
                    "x",
                ],  # This should be fine, because that's what hv.Image expects
                coords={
                    "x": coordinates_xy[0],
                    "y": coordinates_xy[1],
                },
                name="value",
            )

            return mask

        logger.debug("Updating selection mask")
        self.mask = lasso_to_mask(geometry, mask_shape)

    @(
        param.depends(
            "mask",  # variable
        )
    )
    def _plot_mask(self):
        """
        Return an HoloViews Image overlay of the current mask.

        The mask should be a boolean array of the same shape as the image.
        True pixels are highlighted (e.g., red), False pixels are transparent.
        """
        if self.mask is None:
            # Nothing to overlay
            return hv.Image(np.zeros((1, 1)))  # dummy invisible image

        # Convert boolean mask to 0/1
        mask_data = self.mask.astype(int)

        # Create an HoloViews Image
        mask_img = hv.Image(mask_data[::-1])

        # Style: red overlay, transparent where mask=0
        mask_img = mask_img.opts(
            cmap=["grey", "red"],  # 0 → grey, 1 → red
            alpha=0.4,  # overall transparency
            framewise=True,
            tools=[],  # no tools for mask
        )

        return mask_img

    @pn.depends("_plot_data", "_plot_mask")
    def _plot_masked_data(self):
        data = self._plot_data()
        mask = self._plot_mask()
        return data * mask

    def _update_click_param(self, x, y):
        """
        This function takes the (x,y) coordinate from a click on the displayed picture,
        and converts it back into the (z,y,z) coordinates of the dataset
        """
        logger.debug(f"Clicked {time.time()}")

        # Getting (z, y, x) in the choosen coordinate system (either px or real_units)
        horizontal_coord = x
        vertical_coord = y
        match self.img_axis_1:
            case "x":
                x = horizontal_coord
            case "y":
                y = horizontal_coord
            case "z":
                z = horizontal_coord

        match self.img_axis_2:
            case "x":
                x = vertical_coord
            case "y":
                y = vertical_coord
            case "z":
                z = vertical_coord

        match self.img_axis_3:
            # self.img_axis_3_slice is an index, so we need to convert it into the units used
            case "x":
                x = self.img_axis_3_slice * self.x_px.value
            case "y":
                y = self.img_axis_3_slice * self.y_px.value
            case "z":
                z = self.img_axis_3_slice * self.z_px.value

        # === weird WORKAROUND ===
        # - this function is being called by stream from Holoview
        # - it's updating a param variable
        # - this param variable is linked to another one, that is used to trigger stuff
        #
        # *However*: because the initial event comes from Holoviews, it
        # seems like there's some kind of 'lock' (either on bokeh model, or some batch_process from panel)  and the downstream function
        # don't update the GUI at the time they're supposed too
        # (in particular, some widget.loading = True was displaying/updating at the *end* of the function call, not immediately)
        #
        # So the workaround is:
        # - call add_periodic_callback with a function that will update the param (and trigger the downstream stuff)
        #
        # This has been tested with `panel serve` and `panel convert`

        def _panel_update():

            self.dataset_zyx_click = (
                round(z / self.z_px.value),
                round(y / self.y_px.value),
                round(x / self.x_px.value),
            )
            unit = f"(z={z} {self.z_px.units}, y={y} {self.y_px.units}, x={x} {self.x_px.units})"
            index = f"(z={ self.dataset_zyx_click[0]}, y={ self.dataset_zyx_click[1]}, x={self.dataset_zyx_click[2]})"
            user_msg = f"Clicked on pixel: <br/> 🌍: {unit} <br/> 🔢: {index}"
            logger.info(user_msg)
            pn.state.notifications.info(user_msg)

        pn.state.add_periodic_callback(_panel_update, period=200, count=1)

    @(
        param.depends(
            "img_dataset",  # variable
            "_update_axis_1",  # func
            "_update_axis_2",  # func
            "_update_axis_3",  # func
            "img_axis_3_slice",  # variable",
            watch=True,
        )
    )
    @only_on_change(
        "img_dataset",  # variable
        "_update_axis_1",  # func
        "_update_axis_2",  # func
        "_update_axis_3",  # func
        "img_axis_3_slice",  # variable"
    )
    def _compute_histogram(self):
        # Seperate function, so we don't recompute the histogram
        # unless necessary
        frame = self._get_datasetslice()
        self.histogram = frame.hist(adjoin=False)

    @param.depends(
        "bls_file", watch=True
    )  # always update the colorange when a new file is loaded
    @only_on_change(
        "img_dataset",  # variable
        "_update_axis_1",  # func
        "_update_axis_2",  # func
        "_update_axis_3",  # func
        "img_axis_3_slice",  # variable"
    )
    def _update_colorrange(self):
        frame = self._get_datasetslice()
        self.param.colorrange.bounds = frame.range(frame.vdims[0])
        self.colorrange = frame.range(frame.vdims[0])

    # this function only updates the colorange
    # if autoscale is on
    @(
        param.depends(
            "img_dataset",  # variable
            "_update_axis_1",  # func
            "_update_axis_2",  # func
            "_update_axis_3",  # func
            "img_axis_3_slice",  # variable",
            watch=True,
        )
    )
    def _autoscale_colorrange(self):
        if self.autoscale:
            self._update_colorrange()

    @(param.depends("_compute_histogram", "colorrange", watch=True))
    def _overlay_histogram(self):
        # Create vertical lines at the colorrange limits
        self.vlines = hv.Overlay(
            [
                hv.VLine(self.colorrange[0])
                .opts(color="red", line_dash="dotted")
                .opts(axiswise=True),
                hv.VLine(self.colorrange[1])
                .opts(color="red", line_dash="dotted")
                .opts(axiswise=True),
            ]
        ).opts(axiswise=True)

        return (self.histogram * self.vlines).opts(axiswise=True)

    def download_tiff(self):
        """
        Converts the current selected and displayed data into a tiff file.

        The file is saved in a temporary directory, and
        it's path/name if returned. panel.widget.FileDownload will then
        automatically download the file when the user clicks on the button.
        """
        import tempfile
        import os

        if self.bls_data is None or self.bls_analysis is None:
            logger.error("No data loaded, cannot download tiff")
            return
        logger.info("TODO - download as tiff")

        # temp fix: filename retuns the full path of the file
        bls_file_name = os.path.basename(self.bls_file.filename)
        filename = f"{bls_file_name}_{self.bls_data.get_name()}_{self.bls_analysis.get_name()}_{self.result_peak.name}.ome.tif"
        tmpdir = tempfile.mkdtemp()
        file_path = os.path.join(tmpdir, filename)
        logger.info(f"Saving tiff to {file_path}")
        path = self.bls_analysis.save_image_to_OMETiff(
            self.result_quantity, self.result_peak, index=0, filename=file_path
        )

        logger.info(f"Saved tiff to {path}")
        self.result_download.filename = filename
        return file_path

    def __panel__(self):
        """Use some fancier widget for some parameters"""

        self.result_index_dropdown = pn.widgets.Select.from_param(
            self.param.result_index, width=150
        )
        self.result_quantity_dropdown = pn.widgets.Select.from_param(
            self.param.result_quantity, width=150
        )
        self.result_peak_dropdown = pn.widgets.Select.from_param(
            self.param.result_peak, width=150
        )

        self.result_download = pn.widgets.FileDownload(
            name="Click to start download of data",
            filename="brimview_default.tiff",
            label="Export as OME-tiff",
            button_type="primary",
            auto=True,
            callback=self.download_tiff,
        )

        self.result_options = pn.Card(
            pn.FlexBox(
                self.result_index_dropdown,
                self.result_quantity_dropdown,
                self.result_peak_dropdown,
                self.result_download,
            ),
            title="Result display selection",
            collapsed=False,
            collapsible=True,
            sizing_mode="stretch_width",
            margin=5,
        )

        colormap_picker = pn.widgets.ColorMap.from_param(
            self.param.colormap, options=get_linear_colormaps(), ncols=3
        )
        autoscale_checkbox = pn.widgets.Checkbox.from_param(
            self.param.autoscale, name="Autoscale"
        )
        colorrange_picker = pn.widgets.RangeSlider.from_param(
            self.param.colorrange,
            start=0,
            end=1,
            step=0.01,
            value_throttled=0.01,
            disabled=self.autoscale,
        )
        rendering_options = pn.Card(
            pn.FlexBox(
                colormap_picker,
                autoscale_checkbox,
                colorrange_picker,
                pn.pane.HoloViews(self._overlay_histogram),
                align_items="center",
            ),
            title="Rendering options",
            collapsed=True,
            collapsible=True,
            sizing_mode="stretch_width",
            margin=5,
        )

        # add a callback function which is called when the
        # autoscale_checkbox is toggled
        @param.depends(self.param.autoscale, watch=True)
        def autoscale_toggled(value):
            self.param.autoscale = value
            colorrange_picker.disabled = self.autoscale

        # Seems like we need to manually update the widget's bounds
        self.img_axis_3_slice_widget = HorizontalEditableIntSlider.from_param(
            self.param.img_axis_3_slice,
            format="0",
            name="3rd axis",
            width=150,
            fixed_end=0,
            fixed_start=0,  # These will be updated in _update_axis_3
            disabled=True,
            margin=5,
        )
        self.img_axis_3_slice_widget.tooltip_text = "Change which slice is displayed"

        axis_options = pn.Card(
            pn.FlexBox(
                # RadioButton has no working name
                pn.widgets.Select.from_param(self.param.img_axis_1, width=150),
                pn.widgets.Select.from_param(self.param.img_axis_2, width=150),
                pn.Column(
                    pn.widgets.Select.from_param(
                        self.param.img_axis_3, disabled=True, width=150
                    ),
                ),
                pn.widgets.Checkbox.from_param(self.param.use_physical_units),
                self.phys_unit_widget,
            ),
            title="Axis options",
            collapsed=True,
            collapsible=True,
            sizing_mode="stretch_width",
            margin=5,
        )

        main_card = pn.Card(
            pn.Row(self.img_axis_3_slice_widget, align="center"),
            pn.pane.HoloViews(self._plot_masked_data, sizing_mode="stretch_width"),
            self.result_options,
            axis_options,
            rendering_options,
        )
        self.rewrite_card_header(main_card)
        return main_card
