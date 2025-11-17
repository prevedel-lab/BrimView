
import subprocess
import pathlib
import re
import shutil

from importlib.metadata import version
import warnings

# === Helper functions ===

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
    lines = ["import micropip"]
    for name, version in inject_mock_packages:
        lines.append(f'micropip.add_mock_package("{name}", "{version}")')
    python_code = "\n".join(lines)
    js_code = f"self.pyodide.runPython(`\n{python_code}\n`);"
    return js_code

def replace_library_with_url(js_path, lib_name, custom_url):
    with open(js_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Match the env_spec array
    pattern = re.compile(r"(const\s+env_spec\s*=\s*\[)(.*?)(\])", re.DOTALL)
    match = pattern.search(content)
    if not match:
        raise ValueError("env_spec array not found in the JS file.")

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

    if not replaced:
        raise ValueError(f"Library '{lib_name}' not found in env_spec.")

    # Reconstruct env_spec string with quoted values
    new_array = ',\n    '.join(f"{i}" for i in items)
    new_content = f"{match.group(1)}\n    {new_array}\n{match.group(3)}"

    # Replace original env_spec in the JS content
    updated_content = content[:match.start()] + new_content + content[match.end():]

    with open(js_path, 'w', encoding='utf-8') as f:
        f.write(updated_content)

    print(f"Replaced '{lib_name}' with '{custom_url}' in {js_path}")

def replace_pyodide_version(js_path, new_version):
    with open(js_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Match the importScripts Pyodide line
    pattern = re.compile(r'importScripts\(["\']https://cdn\.jsdelivr\.net/pyodide/v[\d\.]+/pyc/pyodide\.js["\']\);')

    if not pattern.search(content):
        raise ValueError("Pyodide importScripts line not found in the JS file.")

    # Replace version in the matched URL
    replacement = f'import {{ loadPyodide }} from "https://cdn.jsdelivr.net/pyodide/v{new_version}/pyc/pyodide.mjs";'
    #replacement = f'importScripts("https://cdn.jsdelivr.net/pyodide/v{new_version}/pyc/pyodide.js");'
    updated_content = pattern.sub(replacement, content)

    with open(js_path, 'w', encoding='utf-8') as f:
        f.write(updated_content)

    print(f">> Updated Pyodide version to v{new_version} in {js_path}")


# === Settings ===
project_file = "./src/index.py"
no_jspi_file = "./src/no_jspi.html"
output_dir = "pyodide"
inject_mock_packages = [("zarr", "3.1.2"), ("bokeh-sampledata","2025.0")]
overwrite_package_path = [
    #("brimfile", "toAbsoluteUrl('./brimfile-1.3.1-py2.py3-none-any.whl')"),
    #("brimfile", "'http://localhost:8000/pyodide/brimfile-1.1.1-py2.py3-none-any.whl'"),
    ("brimview_widgets", "toAbsoluteUrl('./brimview_widgets-0.3.2-py3-none-any.whl')"),
    #("bls_panel_app_widgets", "'http://localhost:8000/dist/bls_panel_app_widgets-0.0.1-py3-none-any.whl'"),
]


injection_file = f"./src/zarr_wrapper.js"  # The file you want to prepend
injection_function= " \
function toAbsoluteUrl(relativePath, baseUrl = self.location.href) {  \
  return new URL(relativePath, baseUrl).href; \
}"

fileinput_clause = " else if (msg.type === 'load_file') {console.log('[From worker - got \"load_file\" msg]'); loadZarrFile(msg.file); self.postMessage({ type: 'file_loaded'});} "

src_zarr_file = f"./src/zarr_file.js"
dst_zarr_file = pathlib.Path(output_dir) / "zarr_file.js"

use_compiled_flag = True  # Set to True if you want to compile the worker script
pyodide_version = "0.28.0"  # Specify the desired Pyodide version -- needed for compiled flag

# make sure we are running a supported panel version
check_panel_version()

# Determine output JS filename from project_file
worker_script = f"{output_dir}/{pathlib.Path(project_file).with_suffix('.js').name}"
html_page = f"{output_dir}/{pathlib.Path(project_file).with_suffix('.html').name}"
# === Step 1: Convert to Pyodide worker ===
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

# === Step 2: Read original code and prepended JS ===
print(">> Reading injection files...")
prepend_code = pathlib.Path(injection_file).read_text()
worker_code = pathlib.Path(worker_script).read_text()


# === Step 3.1: Generate mock package injection code
print(">> Injecting mock packages before micropip install")
pattern = r"(const env_spec\s*=\s*\[.*?\])"
match = re.search(pattern, worker_code, re.DOTALL)

if not match:
    raise RuntimeError("❌ Could not find 'const env_spec = [...]' block to inject before.")

# Inject before the env_spec declaration
injected_block = f"{generate_mock_package_injection(inject_mock_packages)}\n  {match.group(1)}"
patched_body = worker_code[:match.start()] + injected_block + worker_code[match.end():]

# === Step 3.2: Inject the function call before runPythonAsync
print(">> Injecting function call before runPythonAsync...")
pattern = r"(const \[docs_json, render_items, root_ids\] = await self\.pyodide\.runPythonAsync\(code\))"
match = re.search(pattern, patched_body)

if not match:
    raise RuntimeError("❌ Could not find 'await self.pyodide.runPythonAsync(code)' block to inject before.")

# === Step 4: Inject the conditional clause for the file input
print(">> Injecting function call into Worker...")
pattern = r"(else if \(msg\.type === 'patch'\))"
match = re.search(pattern, patched_body)
if not match:
    raise RuntimeError(r"❌ Could not find 'else if \(msg\.type === 'patch'\)' block to inject before.")
# Insert function call before await line
injected_block = f"{fileinput_clause}\n  {match.group(1)}"
patched_body = patched_body[:match.start()] + injected_block + patched_body[match.end():]

# === Step 5: Combine and save
print(">> Saving patched worker script...")
final_code = patched_body + "\n\n" + prepend_code.rstrip() + "\n\n" + injection_function + "\n\n" 
pathlib.Path(worker_script).write_text(final_code)


# === Step 6: Copy zarr_file.js to output folder
print(">> Copying zarr_file.js to output folder...")
src_zarr_file_txt = pathlib.Path(src_zarr_file).read_text()
with open(dst_zarr_file, 'w') as f:
    f.write(src_zarr_file_txt)

# === Step 7: Replace library with URL in worker script
print(">> Replacing library with URL in worker script...")
for overwrite in overwrite_package_path:
    lib_name, custom_url = overwrite
    replace_library_with_url(worker_script, lib_name, custom_url)

# === Step 8: Replace Pyodide version in worker script
if use_compiled_flag:
    print(">> Replacing Pyodide version in worker script...")
    replace_pyodide_version(worker_script, pyodide_version)

# === 9: Replace 
print(">> Replacing worker init inside index.html...")
html_path = pathlib.Path(html_page)
text = html_path.read_text()
pyodideWorker_replacement_text = 'if (!(typeof WebAssembly !== "undefined" && "Suspending" in WebAssembly)) {window.location.replace("./no_jspi.html")};\n\t\t\t' \
'const pyodideWorker = new Worker("./index.js", {type: "module"});'
text = text.replace('const pyodideWorker = new Worker("./index.js");', 
            pyodideWorker_replacement_text)
html_path.write_text(text)

# === Step 10: Copy zarr_file.js to output folder
print(">> Copying no_jspi.html to output folder...")
shutil.copy2(no_jspi_file, output_dir)
print(f"✅ Build complete. Patched worker saved to {worker_script}")
