import { ZarrFile, init_file } from "https://cdn.jsdelivr.net/gh/prevedel-lab/brimfile@main/src/js/zarr_file.js";

// Loads the Zarr and create a bls_file in the globals of pyodide
/**
 * Initializes a Zarr-backed Brillouin file in Pyodide from the selected browser files.
 *
 * This creates the JavaScript Zarr file wrapper, exposes it to Python, builds the
 * corresponding `brimfile.File`, and stores it through `CustomJSFileInput` for later use.
 *
 * @param {FileList|File[]} file_list Non-empty browser file input accepted by `init_file()`.
 * For a folder-based Zarr dataset, pass the `FileList` or array of `File` objects returned by
 * a directory picker, where each file includes `webkitRelativePath` or `relativePath` so the
 * folder hierarchy can be reconstructed; the first path segment is used as the dataset name.
 * A single `.zip` file is also supported; in that case a list containing just that file should be passed.
 * @returns {boolean} Returns `true` after the Pyodide-side file object has been created.
 */
function loadZarrFile(file_list) {
    const {zarr_file_js , filename}  = init_file(file_list)

    const locals = pyodide.toPy({ zarr_file_js: zarr_file_js, zarr_filename: filename });
    pyodide.runPython(`
        import brimfile as bls

        from brimfile.file_abstraction import _zarrFile
        from brimview_widgets import CustomJSFileInput
        
        zf = _zarrFile(zarr_file_js, filename=zarr_filename)
        bls_file = bls.File(zf)
        CustomJSFileInput().set_global_bls(bls_file)
    `, { locals });
    return true;
}
//make sure loadZarrFile is in the global scope
self.loadZarrFile = loadZarrFile;