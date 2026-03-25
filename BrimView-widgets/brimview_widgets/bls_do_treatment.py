import panel as pn
import holoviews as hv
import param
import asyncio
import scipy
import time

import numpy as np
import brimfile as bls
from HDF5_BLS_treat import treat as bls_treat
import scipy.optimize

from .utils import catch_and_notify
from .logging import logger

from .progress_widget import ProgressWidget
from .bls_file_input import BlsFileInput


class BrillouinPeakEstimate(pn.viewable.Viewer):
    position = param.Number(default=0.0, label="Position (GHz)")
    normalizing_window = param.Number(default=2.0, label="Window (GHz) for normalizing")
    fitting_window = param.Number(default=3.0, label="Window (GHz) for fitting")
    type_pnt = param.Selector(
        objects=["Stokes", "Anti-Stokes", "Other"], default="Other"
    )
    bound_shift = param.Range((-10, 10))
    bound_linewidth = param.Range((0, 2))

    def __init__(self, **params):
        super().__init__(**params)

    def __panel__(self):
        """
        Create a Panel widget for the brillouin peak.
        """
        return pn.Param(self.param, show_name=False, width=300)


class BrillouinPeaks(pn.viewable.Viewer):
    peaks = param.List(default=[], item_type=BrillouinPeakEstimate)

    def __init__(self, **params):
        super().__init__(**params)
        self._peak_watchers = {}  # peak -> list of watchers
        self.tabs = pn.Tabs(closable=False)
        self.add_peak(None, position=-6.0, type_pnt="Anti-Stokes", bound_shift=(-8, -4))
        self.add_peak(None, position=6.0, type_pnt="Stokes", bound_shift=(4, 8))

    def _manual_param_trigger(self, event):
        self.param.trigger("peaks")
        # logger.debug(f"Peak '{event.obj}' param '{event.name}' changed to {event.new}")

    def add_peak(self, event, **params):
        """
        Add a new BrillouinPeakEstimate to the list of peaks.
        """
        n_peaks = len(self.tabs)
        # If a position is provided, use it for the new peak
        peak = BrillouinPeakEstimate(name=f"Peak {n_peaks + 1}", **params)
        self.peaks.append(peak)
        self._watch_peak_params(peak)
        logger.debug(self.peaks)
        self.tabs.append((peak.name, peak))
        self._manual_param_trigger(None)  # Trigger the peaks parameter change

    def _watch_peak_params(self, peak):
        watchers = []
        for name in peak.param.objects():
            watcher = peak.param.watch(self._manual_param_trigger, name)
            watchers.append(watcher)
        self._peak_watchers[peak] = watchers

    def remove_peak(self, event):
        """
        Remove the last BrillouinPeakEstimate from the list of peaks.
        """
        if len(self.tabs) > 1:
            peak = self.peaks.pop()
            self._unwatch_peak_params(peak)

            self.tabs.pop()

            if self.tabs.active >= len(self.tabs.objects):
                self.tabs.active = len(self.tabs.objects) - 1
            # self.tabs.active = len(self.tabs) - 1  # Set the last tab as active
        else:
            logger.info("Cannot remove the last peak.")

    def _unwatch_peak_params(self, peak):
        watchers = self._peak_watchers.pop(peak, [])
        for watcher in watchers:
            peak.param.unwatch(watcher)

    def get_hv_vspans(self):
        start = []
        end = []
        for peak in self.peaks:
            start.append(peak.position - peak.fitting_window / 2)
            end.append(peak.position + peak.fitting_window / 2)
        return hv.VSpans((start, end))

    def __panel__(self):
        """
        Create a Panel widget for the brillouin peak.
        """
        add_peak = pn.widgets.Button(
            name="Add Brillouin Peak",
            on_click=self.add_peak,
        )
        remove_peak = pn.widgets.Button(
            name="Remove Brillouin Peak",
            on_click=self.remove_peak,
        )
        return pn.Card(
            self.tabs,
            add_peak,
            remove_peak,
            title="Brillouin Peaks",
            # sizing_mode="stretch_width",
            margin=5,
        )


class BLSTreatOptions(pn.viewable.Viewer):
    _available_models = list(bls_treat.Models().models.keys())
    model_fit = param.Selector(
        objects=_available_models,
        default=_available_models[0],
    )
    threshold_noise = param.Number(default=0.05)

    def __init__(self, **params):
        super().__init__(**params)

    def __panel__(self):
        return pn.Card(
            pn.widgets.Select.from_param(self.param.model_fit),
            pn.widgets.NumberInput.from_param(self.param.threshold_noise),
            title="General fitting options",
            margin=5,
        )


class BlsDoTreatment(pn.viewable.Viewer):

    peaks_for_treament = BrillouinPeaks()
    bls_options = BLSTreatOptions()

    bls_data = param.ClassSelector(class_=bls.Data, default=None, allow_refs=True)
    bls_file = param.ClassSelector(
        class_=bls.File, default=None, allow_refs=True
    )  # usefull to keep the reference, in case we want to get some metadata

    # Fit parameters
    x_stokes_range = param.Range((-10, 0))
    x_antistokes_range = param.Range((0, 10))

    # Parameter to display the progress of the processing
    n_spectra = param.Integer(default=100, label="Number of spectra to process")
    processing_spectra = param.Integer(default=0, label="Current spectrum index")

    mean_spectra = param.Tuple(
        default=(None, None, None, None, None),
        doc="Tuple of (common_freq, mean_spectrum, std_spectrum, frequency_units, PSD_units)",
    )

    def __init__(self, Bh5file: BlsFileInput, **params):
        # This needs to be called before some pn.depends(init=True) functions
        self.plot_pane = pn.pane.HoloViews()

        self._bls_treatment_lock = asyncio.Lock()
        super().__init__(**params)
        # Explicit annotation, because param and type hinting is not working properly
        self.bls_reload_file = Bh5file.reload_file
        self.bls_data: bls.Data = Bh5file.param.data
        self.bls_file: bls.File = Bh5file.param.bls_file

        self.progress_widget = ProgressWidget(step_interval=100, min_interval=1)
        self.spectrum_processing_limit = None

    def button_click(self, event):
        """
        Handle button click event to process data.
        """
        logger.debug("Button clicked!")
        pn.state.execute(self.process_and_save_treatment)

    @catch_and_notify(prefix="<b>_process_and_save_treatment</b> - ")
    async def process_and_save_treatment(self):
        self.data_processed = False
        # await self._process_data()
        await self._bls_treatement()
        await self._save_bls_treatment()

    @catch_and_notify(prefix="<b>Treatment: </b>")
    async def _bls_treatement(self):
        if self._bls_treatment_lock.locked():
            raise RuntimeError("BLS treatment is already running!")

        async with self._bls_treatment_lock:
            if self.bls_data is None:
                return
            (PSD, frequency, PSD_units, frequency_units) = self.bls_data.get_PSD_as_spatial_map(broadcast_frequency=True)
            self._psd_shape = PSD.shape # Storing orignal shape to unflatten later
            PSD_flat = PSD.reshape(-1, PSD.shape[-1])
            freq_flat = frequency.reshape(-1, frequency.shape[-1])

            if self.spectrum_processing_limit is not None:
                # Limit the number of spectra to process
                PSD_flat = PSD_flat[: self.spectrum_processing_limit]
                freq_flat = freq_flat[: self.spectrum_processing_limit]

            # Converting to 1D freq array    
            freq_flat = freq_flat[0, :]  # Assuming the frequency is the same across all spectra, we take the first one
            
            # bls_treat expects the frequency to be ordered from low to high
            # brimfile array can be anything, so we sort it just in case
            sort_indices = np.argsort(freq_flat)
            freq_flat = freq_flat[sort_indices]
            PSD_flat = PSD_flat[:, sort_indices]
            logger.debug("Frequency axis for BLS treatment: %s", freq_flat)
            self.bls_treat = bls_treat.Treat(frequency=freq_flat, PSD=PSD_flat)

            # import matplotlib.pyplot as plt  # DEBUG remove later

            # Manual type hinting
            peaks: list[BrillouinPeakEstimate] = self.peaks_for_treament.peaks

            positions = [peak.position for peak in peaks]
            window_points = [peak.normalizing_window for peak in peaks]
            # Adding the points to the treat object
            for p, w in zip(positions, window_points):
                logger.debug(f"Adding point for normalization: position={p}, window={w}")
                self.bls_treat.add_point(
                    position_center_window=p, type_pnt="Other", window_width=w
                )
            # Applying the normalization: the lowest 5% of the data is averaged to extract the offset and then the intensity array is divided by the average of the intensity of the two peaks so as to normalize the amplitude of the peaks to 1
            # self.bls_treat.normalize_data(
            #     threshold_noise=self.bls_options.threshold_noise
            # )  # Note: This function clears the points stored in memory of the treat module

            # Selecting the points for the fitting
            positions = [peak.position for peak in peaks]
            window_fit = [peak.fitting_window for peak in peaks]
            tpe_points = [
                peak.type_pnt for peak in peaks
            ]  # The types of peaks that we fit - important to then combine the results into one value per spectrum
            for p, w, t in zip(positions, window_fit, tpe_points):
                self.bls_treat.add_point(
                    position_center_window=p, type_pnt=t, window_width=w
                )

            # Defining the model for fitting the peaks
            logger.debug(self.bls_options.model_fit)
            self.bls_treat.define_model(
                model=self.bls_options.model_fit, elastic_correction=False
            )  # You can also try with "Lorentzian" model and add elastic corrections by setting the parameter to True for both lineshapes.

            # Estimating the linewidth from selected peaks
            self.bls_treat.estimate_width_inelastic_peaks(max_width_guess=2)

            # Fitting all the selected inelastic peaks with multiple peaks fitting
            bound_shift = [
                [peak.bound_shift[0], peak.bound_shift[1]] for peak in peaks
            ]  # Boundaries for the shift
            bound_linewidth = [
                [peak.bound_linewidth[0], peak.bound_linewidth[1]] for peak in peaks
            ]  # Boundaries for the linewidth
            self.bls_treat.multi_fit_all_inelastic(
                guess_offset=True,
                update_point_position=True,
                bound_shift=bound_shift,
                bound_linewidth=bound_linewidth,
            )

            self.bls_treat._progress_callback = self.progress_widget.update
            self.progress_widget.start(
                total=100
            )  # Values doesn't matter, will be overwritten by callback function
            t0 = time.time()
            # TODO: convert this into an async function / generator, that yields the current iteration ?
            await asyncio.to_thread(self.bls_treat.apply_algorithm_on_all)
            tf = time.time() - t0
            self.progress_widget.finish()

            logger.debug(f"shift: {self.bls_treat.shift.shape}")
            logger.debug(f"amplitude: {self.bls_treat.amplitude.shape}")
            logger.debug(f"linewidth: {self.bls_treat.linewidth}")

            # Combining the two fitted peaks together here weighing the result on the standard deviation of the shift
            # self.bls_treat.combine_results_FSR(
            #    FSR=15,
            #    keep_max_amplitude=False,
            #    amplitude_weight=False,
            #    shift_std_weight=True,
            # )

            logger.info(f"Time for fitting all spectra: {tf:.2f} s")

            logger.debug(self.bls_treat.shift)
            logger.info(
                f"Average time for a single spectrum: {1e3*tf/np.prod(len(self.bls_treat.shift)):.2f} ms"
            )

    @catch_and_notify(prefix="<b>Save treatment: </b>")
    async def _save_bls_treatment(self):
        if self.bls_treat is None:
            raise ValueError("No BLS treatment available.")
        logger.debug(f"shift: {self.bls_treat.shift.shape}")
        logger.debug(f"amplitude: {self.bls_treat.amplitude.shape}")
        logger.debug(f"linewidth: {self.bls_treat.linewidth.shape}")

        fitted_peaks = []
        model = self.bls_options.model_fit
        match model:
            case "Lorentzian":
                model = bls.Data.AnalysisResults.FitModel.Lorentzian
            case "Lorentzian elastic":
                model = bls.Data.AnalysisResults.FitModel.Undefined
            case "DHO":
                model = bls.Data.AnalysisResults.FitModel.DHO
            case "DHO elastic":
               model = bls.Data.AnalysisResults.FitModel.Undefined
            case "Gaussian":
                model = bls.Data.AnalysisResults.FitModel.Gaussian
            case _:
                model = bls.Data.AnalysisResults.FitModel.Undefined

        time_str = time.strftime("%Y%m%d-%H%M%S")
        name = f"BrimView_{model}_{time_str}"
        for (
            shift,
            amplitude,
            linewidth,
            offset,
        ) in zip(  # they are in the shape (n_spectra, n_peaks)
            self.bls_treat.shift.T,
            self.bls_treat.amplitude.T,
            self.bls_treat.linewidth.T,
            self.bls_treat.offset.T,
        ):
            # unflattening the results
            if self.spectrum_processing_limit:
                self.n_spectra = np.prod(self._psd_shape[:-1])
                # Add 0 to the end of the flat arrays to match the original shape, for the spectra that were not processed
                logger.debug(
                    "Peak shapes before padding - shift: %s, amplitude: %s, linewidth: %s, offset: %s",
                    shift.shape,
                    amplitude.shape,
                    linewidth.shape,
                    offset.shape,
                )
                shift = np.pad(shift, ((0, self.n_spectra - shift.shape[0])), mode="constant", constant_values=0.0)
                amplitude = np.pad(amplitude, ((0, self.n_spectra - amplitude.shape[0])), mode="constant", constant_values=0.0)
                linewidth = np.pad(linewidth, ((0, self.n_spectra - linewidth.shape[0])), mode="constant", constant_values=0.0)
                offset = np.pad(offset, ((0, self.n_spectra - offset.shape[0])), mode="constant", constant_values=0.0)
            
            shift = shift.reshape(self._psd_shape[:-1])  
            amplitude = amplitude.reshape(self._psd_shape[:-1])
            linewidth = linewidth.reshape(self._psd_shape[:-1])
            offset = offset.reshape(self._psd_shape[:-1])
            
            fitted_peaks.append(
                {
                    "shift": shift,
                    "shift_units": "GHz",
                    "width": linewidth,
                    "width_units": "Hz",
                    "amplitude": amplitude,
                    "amplitude_units": "a.u.",
                    "offset": offset,
                    "offset_unit": "u.a",
                }
            )
        if len(fitted_peaks) == 1:
            self.bls_data.create_analysis_results_group(
                (fitted_peaks[0]),
                fit_model=model,
                name=name,
            )
        elif len(fitted_peaks) == 2:
            # If fitted_peak[1] offset is 0, then use the offset of fitted_peak[0] for both peaks, otherwise we have to save the offset of the two peaks separately
            peak_anti_stokes = fitted_peaks[0]
            peak_stokes = fitted_peaks[1].copy()
            if np.allclose(peak_stokes["offset"], 0.0):
                peak_stokes["offset"] = peak_anti_stokes["offset"].copy()

            self.bls_data.create_analysis_results_group(
                peak_anti_stokes,
                peak_stokes,
                fit_model=model,
                name=name,
            )
        else:
            raise Exception("More than 2 peaks fitted, unsure how to save that")
        self.bls_reload_file()

    @param.depends("bls_data", watch=True)
    def _update_widget(self):
        if self.bls_data is None:
            self.mean_spectra_button.disabled = True
            self.btn_process_data.disabled = True
        else:
            self.mean_spectra_button.disabled = False
            self.btn_process_data.disabled = False
            (PSD, frequency, PSD_units, frequency_units) = self.bls_data.get_PSD_as_spatial_map(broadcast_frequency=True)
            self.mean_spectra_n_samples.end = np.prod(PSD.shape[:-1])
            self.mean_spectra_n_samples.start = 1
            logger.debug(PSD.shape)

    @catch_and_notify(prefix="<b>Compute mean spectra: </b>")
    def compute_mean_spectra(self, event):
        (PSD, frequency, PSD_units, frequency_units) = self.bls_data.get_PSD_as_spatial_map(broadcast_frequency=True)

        logger.debug(f"PSD shape : {PSD.shape}")
        logger.debug(f"freq shape : {frequency.shape}")

        # generate average PSD - last dimension is the frequency
        num_spectra = np.prod(PSD.shape[:-1])
        n_data_points = PSD.shape[-1]
        freq_min = np.nanmin(frequency)
        freq_max = np.nanmax(frequency)
        common_freq = np.linspace(freq_min, freq_max, n_data_points)  # shape (71,)

        # Flattening 
        PSD_flat = PSD.reshape(-1, PSD.shape[-1])
        freq_flat = frequency.reshape(-1, frequency.shape[-1])

        # we're sampling some spectra
        flat_indices = np.random.choice(
            num_spectra, size=self.mean_spectra_n_samples.value, replace=False
        )

        interpolated_psd = np.empty(
            (len(flat_indices), len(common_freq))
        )  # TODO - this might not work with data with more dimensions
        self.progress_widget.start(
            total=len(flat_indices), task="Computing mean spectra"
        )

        for i, flat_idx in enumerate(flat_indices):
            f = freq_flat[flat_idx]
            p =  PSD_flat[flat_idx]
            interp_func = scipy.interpolate.interp1d(
                f, p, kind="linear", bounds_error=False, fill_value="extrapolate"
            )
            interpolated_psd[i, :] = interp_func(common_freq)
            self.progress_widget.update(i)
            # UI stuff
            # self.progress.value = i  # Yield control to the event loop to update the UI
            # await asyncio.sleep(0)  # allow UI to update

        self.progress_widget.finish()
        mean_spectrum = np.mean(interpolated_psd, axis=0)  # shape (71,)
        std_spectrum = np.nanstd(interpolated_psd, axis=0)

        self.mean_spectra = (
            common_freq,
            mean_spectrum,
            std_spectrum,
            frequency_units,
            PSD_units,
        )

    @param.depends(
        "mean_spectra",
        "peaks_for_treament.param",
        on_init=True,
        watch=True,
    )
    @catch_and_notify(prefix="<b>fit_parameters_help_ui: </b>")
    def fit_parameters_help_ui(self):
        peak_spans = self.peaks_for_treament.get_hv_vspans().opts(
            color="red",
            axiswise=True,  # Give independent axis
        )

        (
            common_freq,
            mean_spectrum,
            std_spectrum,
            frequency_units,
            PSD_units,
        ) = self.mean_spectra
        if common_freq is None and mean_spectrum is None:
            logger.error("Curve is None !")
            plot = peak_spans

        else:
            curve = hv.Curve(
                (common_freq, mean_spectrum),
                hv.Dimension("Frequency", unit=frequency_units),
                hv.Dimension("PSD", unit=PSD_units),
                label=f"Average Spectra",
            ).opts(
                tools=["hover"],
            )
            spread = hv.Spread((common_freq, mean_spectrum, std_spectrum))
            plot = curve * spread * peak_spans
        # plot.opts(responsive=True)
        self.plot_pane.object = plot

    def __panel__(self):
        """Use some fancier widget for some parameters"""
        self.btn_process_data = pn.widgets.Button(
            name="Process Data",
            button_type="primary",
            width=200,
            sizing_mode="stretch_width",
            on_click=self.button_click,
            disabled=True,
        )

        self.mean_spectra_n_samples = pn.widgets.IntInput(
            name="Number of spectra to use", value=50, start=1, end=1000, step=50
        )
        self.mean_spectra_button = pn.widgets.Button(
            name="Compute mean spectra",
            button_type="primary",
            on_click=self.compute_mean_spectra,
            disabled=True,
        )

        return pn.FlexBox(
            pn.FlexBox(
                self.plot_pane,
                pn.Column(self.mean_spectra_n_samples, self.mean_spectra_button),
                self.peaks_for_treament,
                self.bls_options,
            ),
            self.btn_process_data,
            self.progress_widget,
            # title="Create new Treatment",
        )
