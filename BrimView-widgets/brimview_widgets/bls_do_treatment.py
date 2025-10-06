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
        if self.data_processed:
            self._save_treatment()

    @catch_and_notify(prefix="<b>Treatment: </b>")
    async def _bls_treatement(self):
        if self._bls_treatment_lock.locked():
            raise RuntimeError("BLS treatment is already running!")

        async with self._bls_treatment_lock:
            if self.bls_data is None:
                return
            (PSD, frequency, PSD_units, frequency_units) = self.bls_data.get_PSD()
            # TODO: use nD frequency array when the library supports it
            frequency = np.broadcast_to(frequency, PSD.shape)[
                0, :
            ]  # Assuming frequency is 2D, take the first column

            if self.spectrum_processing_limit is not None:
                # Limit the number of spectra to process
                PSD = PSD[: self.spectrum_processing_limit, :]
                frequency = frequency[: self.spectrum_processing_limit]

            self.bls_treat = bls_treat.Treat(frequency=frequency, PSD=PSD)

            # import matplotlib.pyplot as plt  # DEBUG remove later

            # Manual type hinting
            peaks: list[BrillouinPeakEstimate] = self.peaks_for_treament.peaks

            positions = [peak.position for peak in peaks]
            window_points = [peak.normalizing_window for peak in peaks]
            # Adding the points to the treat object
            for p, w in zip(positions, window_points):
                self.bls_treat.add_point(
                    position_center_window=p, type_pnt="Other", window_width=w
                )
            # Applying the normalization: the lowest 5% of the data is averaged to extract the offset and then the intensity array is divided by the average of the intensity of the two peaks so as to normalize the amplitude of the peaks to 1
            self.bls_treat.normalize_data(
                threshold_noise=self.bls_options.threshold_noise
            )  # Note: This function clears the points stored in memory of the treat module

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
    
    async def _process_data(self):
        # Using yield/generator to display real-time process
        # So we also need to store the results in the class

        # TODO: this is still blocking the UI for some reason
        logger.debug(self.bls_data.get_num_parameters())
        i = 0
        self.AS_shift = []
        self.S_shift = []
        self.AS_width = []
        self.S_width = []
        self.AS_Amplitude = []
        self.S_Amplitude = []
        self.data_processed = False
        # max spectrum = 28574
        n_test_spectra = 100
        self.AS_shift = np.array([0.0] * 28574)
        self.S_shift = np.array([0.0] * 28574)
        self.AS_width = np.array([1.0] * 28574)
        self.S_width = np.array([1.0] * 28574)
        self.AS_Amplitude = np.array([2.0] * 28574)
        self.S_Amplitude = np.array([2.0] * 28574)

        (PSD, frequency, PSD_units, frequency_units) = self.bls_data.get_PSD()
        # For testing, we will process only 100 spectra
        self.n_spectra = n_test_spectra
        # self.n_spectra = PSD.shape[0]
        self.processing_spectra = 0
        logger.debug(f"PSD.shape: {PSD.shape}, frequency.shape: {frequency.shape}")
        for i in range(0, self.n_spectra):
            # Let's release the thread so that the UI can update
            # in the proper version, we would do this every x iterations
            await asyncio.sleep(0.01)  # Yield control to the event loop
            # logger.info(f"Processing spectrum {i}")
            self.processing_spectra = i
            start_time = time.perf_counter()

            # yield i
            # Simulate processing the spectrum
            # (PSD, frequency, PSD_units, frequency_units) = self.bls_data.get_spectrum(i)

            mask_as = (frequency[i, :] > self.x_antistokes_range[0]) & (
                frequency[i, :] < self.x_antistokes_range[1]
            )
            AS_x = frequency[i, mask_as]
            AS_y = PSD[i, mask_as]

            mask_s = (frequency[i, :] > self.x_stokes_range[0]) & (
                frequency[i, :] < self.x_stokes_range[1]
            )
            S_x = frequency[i, mask_s]
            S_y = PSD[i, mask_s]

            def lorentzian(x, x0, w):
                return 1 / (1 + ((x - x0) / (w / 2)) ** 2)

            def real_lorentzian(x, shift, width, amplitude):
                return amplitude * lorentzian(x, shift, width)

            (AS_popt, AS_pcov) = scipy.optimize.curve_fit(real_lorentzian, AS_x, AS_y)
            (S_popt, S_pcov) = scipy.optimize.curve_fit(real_lorentzian, S_x, S_y)
            logger.debug(AS_popt)
            # TODO later: actual fit and process the data
            self.AS_shift[i] = AS_popt[0]
            self.AS_width[i] = AS_popt[1]
            self.AS_Amplitude[i] = AS_popt[2]
            self.S_shift[i] = S_popt[0]
            self.S_width[i] = S_popt[1]
            self.S_Amplitude[i] = S_popt[2]
            end_time = time.perf_counter()
            duration = end_time - start_time
            logger.info(f"Async iteration {i} took {duration:.6f} seconds")
        self.data_processed = True

    @catch_and_notify(prefix="<b>Save treatment: </b>")
    async def _save_bls_treatment(self):
        if self.bls_treat is None:
            raise ValueError("No BLS treatment available.")
        logger.debug(f"shift: {self.bls_treat.shift.shape}")
        logger.debug(f"amplitude: {self.bls_treat.amplitude.shape}")
        logger.debug(f"linewidth: {self.bls_treat.linewidth.shape}")

        fitted_peaks = []

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
            self.bls_data.create_analysis_results_group_raw(
                (fitted_peaks[0]),
                name="test1_analysis",
            )
        elif len(fitted_peaks) == 2:
            self.bls_data.create_analysis_results_group_raw(
                (fitted_peaks[0]),
                (fitted_peaks[1]),
                name="test1_analysis",
            )
        else:
            logger.warning("More than 2 peaks fitted, unsure how to save that")
        self.bls_reload_file()

    @catch_and_notify(prefix="<b>Save treatment: </b>")
    def _save_treatment(self):
        """
        Perform the treatment on the data.
        This is a placeholder for the actual treatment logic.
        """
        if self.bls_data is None:
            raise ValueError("No BLS data available for treatment.")
        logger.debug(f"max AS shift: {np.max(self.AS_shift)}, min AS shift: {np.min(self.AS_shift)}")
        logger.debug(self.AS_shift.shape)
        logger.debug(self.S_shift.shape)
        # Example treatment: Normalize the data
        ar = self.bls_data.create_analysis_results_group_raw(
            (
                {
                    "shift": self.AS_shift,
                    "shift_units": "GHz",
                    "width": self.AS_width,
                    "width_units": "Hz",
                    "amplitude": self.AS_Amplitude,
                    "amplitude_units": "a.u.",
                },
            ),
            (
                {
                    "shift": self.S_shift,
                    "shift_units": "GHz",
                    "width": self.S_width,
                    "width_units": "Hz",
                    "amplitude": self.S_Amplitude,
                    "amplitude_units": "a.u.",
                },
            ),
            name="test1_analysis",
        )
        self.bls_reload_file()
        logger.debug(ar)
        # self.bls_data.data = (
        #    self.bls_data.data - np.mean(self.bls_data.data)
        # ) / np.std(self.bls_data.data)
        logger.info("Treatment applied to BLS data.")

    @param.depends("bls_data", watch=True)
    def _update_widget(self):
        if self.bls_data is None:
            self.mean_spectra_button.disabled = True
            self.btn_process_data.disabled = True
        else:
            self.mean_spectra_button.disabled = False
            self.btn_process_data.disabled = False
            (PSD, frequency, PSD_units, frequency_units) = self.bls_data.get_PSD()
            self.mean_spectra_n_samples.end = PSD.shape[0]
            self.mean_spectra_n_samples.start = 1
            logger.debug(PSD.shape)

    def compute_mean_spectra(self, event):
        (PSD, frequency, PSD_units, frequency_units) = self.bls_data.get_PSD()
        frequency = np.broadcast_to(frequency, PSD.shape)

        logger.debug(f"PSD shape : {PSD.shape}")
        logger.debug(f"freq shape : {frequency.shape}")

        # generate average PSD - last dimension is the frequency
        n_data_points = PSD.shape[1]
        freq_min = np.nanmin(frequency)
        freq_max = np.nanmax(frequency)
        common_freq = np.linspace(freq_min, freq_max, n_data_points)  # shape (71,)

        # we're sampling some spectr
        sample_indices = np.random.choice(
            PSD.shape[0], size=self.mean_spectra_n_samples.value, replace=False
        )

        interpolated_psd = np.empty(
            (len(sample_indices), len(common_freq))
        )  # TODO - this might not work with data with more dimensions
        self.progress_widget.start(
            total=len(sample_indices), task="Computing mean spectra"
        )

        for i, idx in enumerate(sample_indices):
            f = frequency[idx, :]
            p = PSD[idx, :]
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
        logger.debug(self.mean_spectra)
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
