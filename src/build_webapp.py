
import subprocess
import pathlib
import re
import shutil

from importlib.metadata import version
import warnings

# region === Helper functions ===

def check_panel_version():
    panel_version = version("panel")
    print("Installed Panel version:", panel_version)
    major, minor, *_ = map(int, panel_version.split("."))
    if major == 1 and minor>=8:
        if minor>8:
            warnings.warn(f"Panel version {panel_version} was not tested, it might not work as expected")
    else:
        raise ValueError(f"Panel version {panel_version} is not supported")
    
def generate_mock_package_injection(inject_mock_packages):
    lines = []
    for name, version in inject_mock_packages:
        lines.append(f'      micropip.add_mock_package("{name}", "{version}")')
    python_code = "\n".join(lines)
    return python_code

def replace_library_with_url(js_code, lib_name, custom_url):
    # Match the env_spec array
    pattern = re.compile(r"(\s*await\s+micropip\.install\s*\(\[)(.*?)(\]\))", re.MULTILINE)
    match = pattern.search(js_code)
    if not match:
        raise ValueError("'await micropip install' not found in the JS file.")

    # Extract array content and split items (this assumes a comma-separated list of strings)
    array_content = match.group(2)
    #items = [item.strip().strip('"\'') for item in array_content.split(',') if item.strip()]
    items = array_content.split(',')

    # Replace the target library
    replaced = False
    for i, item in enumerate(items):
        item = item.strip().strip('"\'')  # Clean up quotes and whitespace
        # Replace if exact name match (excluding versions for plain names)
        if item == lib_name or item.split('==')[0] == lib_name:
            items[i] = custom_url
            replaced = True
            break

    if not replaced:
        raise ValueError(f"Library '{lib_name}' not found in micropip install.")

    # Reconstruct env_spec string with quoted values
    new_array = ', '.join(f"{i}" for i in items)
    new_content = f"{match.group(1)}{new_array}{match.group(3)}"

    # Replace original list in micropip install
    updated_content = js_code[:match.start()] + new_content + js_code[match.end():]

    print(f"Replaced '{lib_name}' with '{custom_url}'")

    return updated_content

def replace_pyodide_import(js_code, new_version):
    path = "pyc" if use_compiled_flag else "full"
    # Match the importScripts Pyodide line
    pattern = re.compile(fr'importScripts\(["\']https://cdn\.jsdelivr\.net/pyodide/v[\d\.]+/{path}/pyodide\.js["\']\);')

    if not pattern.search(js_code):
        raise ValueError("Pyodide importScripts line not found in the JS file.")

    # Replace version in the matched URL and change to module import
    replacement = f'import {{ loadPyodide }} from "https://cdn.jsdelivr.net/pyodide/v{new_version}/{path}/pyodide.mjs";'
    updated_content = pattern.sub(replacement, js_code)

    print(f">> Updated Pyodide version to v{new_version}")

    return updated_content

# endregion 

# region === Settings ===
inject_mock_packages = [("zarr", "3.1.2"), ("bokeh-sampledata","2025.0")]
overwrite_package_path = [
    ("brimview_widgets", "js.toAbsoluteUrl('./brimview_widgets-0.3.2-py3-none-any.whl')"),
]
project_file = "./src/index.py"
src_zarr_file = f"./src/zarr_file.js"
no_jspi_file = "./src/no_jspi.html"
output_dir = "pyodide"


injection_file = f"./src/zarr_wrapper.js"  # The file you want to prepend
injection_function= "self.toAbsoluteUrl = function(relativePath, baseUrl = self.location.href) { return new URL(relativePath, baseUrl).href;}"

fileinput_clause = " else if (msg.type === 'load_file') {console.log('[From worker - got \"load_file\" msg]'); loadZarrFile(msg.file); self.postMessage({ type: 'file_loaded'});} "

use_compiled_flag = True  # Set to True if you want to use the compiled version of Pyodide (faster but harder to debug and with some limitations, see https://github.com/pyodide/pyodide/issues/3269 )
pyodide_version = "0.28.2"  # Specify the desired Pyodide version, it should match the one used by the current version of panel convert

# endregion 

# make sure we are running a supported panel version
check_panel_version()

# Determine output JS filename from project_file
worker_script = f"{output_dir}/{pathlib.Path(project_file).with_suffix('.js').name}"
html_page = f"{output_dir}/{pathlib.Path(project_file).with_suffix('.html').name}"

# region ===== Step 1: Convert to Pyodide worker
print(">> Converting with panel...")
if use_compiled_flag:
    subprocess.run(
        ["panel", "convert", project_file, "--to", "pyodide-worker", "--out", output_dir, "--compiled"],
        check=True
    )
else:
    subprocess.run(
        ["panel", "convert", project_file, "--to", "pyodide-worker", "--out", output_dir],
        check=True
    )
# endregion

# region ===== Step 2: Modify the index.js file

# === Step 2.1: Read original code and prepended JS ===
print(">> Reading injection files...")
prepend_code = pathlib.Path(injection_file).read_text()
worker_code = pathlib.Path(worker_script).read_text()


# === Step 2.2: Generate mock package injection code and add js import
print(">> Injecting mock packages before micropip install")
pattern = r"^\s*import\s+micropip\s*$"
match = re.search(pattern, worker_code, re.MULTILINE)

if not match:
    raise RuntimeError("❌ Could not find 'import micropip' block to inject after.")

# Inject after the import micropip declaration
injected_block = generate_mock_package_injection(inject_mock_packages)
patched_body = worker_code[:match.end()] + ", js\n" + injected_block + worker_code[match.end():]

# === Step 2.3: Inject the conditional clause for the file input
print(">> Injecting function call into Worker...")
pattern = r"(else if \(msg\.type === 'patch'\))"
match = re.search(pattern, patched_body)
if not match:
    raise RuntimeError(r"❌ Could not find 'else if \(msg\.type === 'patch'\)' block to inject before.")
# Insert function call before await line
injected_block = f"{fileinput_clause}\n  {match.group(1)}"
patched_body = patched_body[:match.start()] + injected_block + patched_body[match.end():]

# === Step 2.4: Replace library with URL in worker script
print(">> Replacing library with URL in worker script...")
for overwrite in overwrite_package_path:
    lib_name, custom_url = overwrite
    patched_body = replace_library_with_url(patched_body, lib_name, custom_url)

# === Step 2.5: Replace Pyodide version in worker script
print(">> Replacing Pyodide import as module in worker script...")
patched_body = replace_pyodide_import(patched_body, pyodide_version)

# === Step 2.6: Combine and save
print(">> Saving patched worker script...")
final_code = patched_body + "\n\n" + prepend_code.rstrip() + "\n\n" + injection_function + "\n\n" 
pathlib.Path(worker_script).write_text(final_code)

# endregion

# region ===== Step 3: Modify the index.html file

# === Step 3.1: Add the check of jspi and create the worker as a module
print(">> Replacing worker init inside index.html...")
html_path = pathlib.Path(html_page)
text = html_path.read_text()
pyodideWorker_replacement_text = 'if (!(typeof WebAssembly !== "undefined" && "Suspending" in WebAssembly)) {window.location.replace("./no_jspi.html")};\n\t\t\t' \
'const pyodideWorker = new Worker("./index.js", {type: "module"});'
text = text.replace('const pyodideWorker = new Worker("./index.js");', 
            pyodideWorker_replacement_text)
html_path.write_text(text)

# endregion

# region ===== Step 4: Copy additional files to the output folder

# === Step 4.1: Copy zarr_file.js to output folder
print(">> Copying zarr_file.js to output folder...")
shutil.copy2(src_zarr_file, output_dir)

# === Step 4.2: Copy no_jspi.html to output folder
print(">> Copying no_jspi.html to output folder...")
shutil.copy2(no_jspi_file, output_dir)

# endregion

print(f"✅ Build complete. Patched worker saved to {worker_script}")
