import * as zarr from "https://cdn.jsdelivr.net/npm/zarrita/+esm";
//Imports for reading zip files
import ZipStore from "https://cdn.jsdelivr.net/npm/@zarrita/storage/zip/+esm";
//imports for reading zarr files from S3 buckets
import FetchStore from "https://cdn.jsdelivr.net/npm/@zarrita/storage/fetch/+esm";
import { XMLParser } from "https://cdn.jsdelivr.net/npm/fast-xml-parser/+esm";

// This function is used to standardize the path by ensuring it ends with a '/' and does not start with one.
function standardize_path(path) {
  if (!path.endsWith('/')) {
    path = path + '/';
  }
  if (path.startsWith('/')) {
    path = path.slice(1);
  }
  return path;
}

class ZarrFile {
  static StoreType = Object.freeze({
    ZIP: 'zip',
    ZARR: 'zarr',
    S3: 'S3',
    AUTO: 'auto'
  })
  constructor() {
    this.ready = false
  }
  is_ready() {
    return this.ready;
  };
  async #wait_for_ready(timeout_ms = 2000) {
    async function sleep(ms) {
      return new Promise(resolve => setTimeout(resolve, ms));
    }
    const start = performance.now();
    while (true) {
      if (this.is_ready()) {
        return;
      }
      else {
        await sleep(1); 
      }
      if (performance.now()-start>timeout_ms) {
        throw new Error(`The zarr file was not ready within the timout of ${timeout_ms}ms`);
      }
    }
  }
  async init(file) {
    this.filename = file.name;
    this.store_type = ZarrFile.StoreType.ZIP;  

    this.store = await ZipStore.fromBlob(file);
    this.root = await zarr.open.v3(this.store, { kind: "group" });  
  }

  async init_from_url(url) {
    this.filename = url;
    this.store_type = ZarrFile.StoreType.S3;

    this.store = new FetchStore(url);
    this.root = await zarr.open.v3(this.store, { kind: "group" });
  }

  /******** Attribute Management ********/
  async get_attribute(full_path, attr_name) {
    await this.#wait_for_ready()
    full_path = standardize_path(full_path);
    const obj = await zarr.open.v3(this.root.resolve(full_path));
    return obj.attrs[attr_name];
  }

  /******** Group Management ********/

  async open_group(full_path){
    await this.#wait_for_ready()
    full_path = standardize_path(full_path);
    if (! await this.object_exists(full_path)) {
      throw new Error(`The object '${full_path}' doesn't exist!`)
    }
    // for now simply return the path since all the functions that accepts a group object require its full path
    return full_path
    //Here is the actual code that would open the group 
    /*
    const group = await zarr.open(this.root.resolve(full_path), { kind: "group" });
    return group
    */
  }

  /******** Dataset Management ********/

  async open_dataset(full_path){
    await this.#wait_for_ready()
    full_path = standardize_path(full_path);
    if (! await this.object_exists(full_path)) {
      throw new Error(`The object '${full_path}' doesn't exist!`)
    }
    // for now simply return the path since the array object itself is not json transferrable
    return full_path
    const arr = await zarr.open.v3(this.root.resolve(full_path), { kind: "array" });
    return arr
  }

  async get_array_slice(full_path, indices){
    await this.#wait_for_ready()
    const array = await zarr.open.v3(this.root.resolve(full_path), { kind: "array" });
    function undef2null(obj) {return obj===undefined?null:obj;}
    let js_indices = [];
    for (let i of indices) {
      js_indices.push(zarr.slice(undef2null(i[0]), undef2null(i[1])))
    }
    const res = await zarr.get(array, js_indices);
    return res
  }

  async get_array_shape(full_path){
    await this.#wait_for_ready()
    const array = await zarr.open.v3(this.root.resolve(full_path), { kind: "array" });
    return array.shape;
  }

  /******** Listing ********/

  async #list_S3keys(full_path){

    function split_path(url, full_path) {
      url = standardize_path(url).slice(0,-1);
      full_path = standardize_path(full_path);

      let path = [];
      const last_slash = url.lastIndexOf('/');
      path.endpoint = url.slice(0, last_slash+1)
      path.object = url.slice(last_slash+1) + '/' + full_path
      return path;
    }
    const path = split_path(this.filename, full_path);

    let queries = "list-type=2&delimiter=/";
    queries += "&prefix="+path.object;

    let url = path.endpoint + "?" + queries;
    url = encodeURI(url);

    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP error: ${response.status}`);
    const xmlText = await response.text();

    // Parse XML using fast-xml-parser
    const parser = new XMLParser();
    const xmlObj = parser.parse(xmlText);

    function ExtractKeyFromPrefix (x) {
        let p = x.Prefix;
        if (p.endsWith('/')) {
            p = p.slice(0, -1)
        }
        p = p.split('/').pop()
        return p;
    }
    // Extract CommonPrefixes
    let prefixes = [];
    if (xmlObj.ListBucketResult && xmlObj.ListBucketResult.CommonPrefixes) {
        const cp = xmlObj.ListBucketResult.CommonPrefixes;
        if (Array.isArray(cp)) {
            prefixes = cp.map(ExtractKeyFromPrefix);
        } else if (cp.Prefix) {
            prefixes = [ExtractKeyFromPrefix(cp.Prefix)];
        }
    }
    return prefixes;
  }
  async list_objects(full_path) {
    await this.#wait_for_ready()
    const objects = [];
    full_path = standardize_path(full_path);

    if (this.store_type == ZarrFile.StoreType.ZIP) {
      const entries = (await this.store.info).entries;
      for (const key of Object.keys(entries)) {
        //console.log(key);
        if (key.startsWith(full_path)) {
          let obj = key.slice(full_path.length);
          obj = obj.split("/")[0];
          if (!obj.endsWith('zarr.json') && !objects.includes(obj)) {
            objects.push(obj);
          }
        } 
      }
    }
    else if (this.store_type == ZarrFile.StoreType.S3) {
      return this.#list_S3keys(full_path);
    }
    else {
      throw new Error(this.store_type + ' is not supported!')
    }
    return objects;
  }

  async object_exists(full_path) {
    await this.#wait_for_ready()
    full_path = standardize_path(full_path);
    try {
      const obj = await zarr.open.v3(this.root.resolve(full_path));
      return true;
    }
    catch (e) {
      return false;
    }
  }

  async list_attributes(full_path) {
    await this.#wait_for_ready()
    full_path = standardize_path(full_path);
    const obj = await zarr.open.v3(this.root.resolve(full_path));
    return Object.keys(obj.attrs);
  }
}

// the function returns immediately but the returned ZarrFile instance is only valid
// when .is_ready()==true
function init_file(file) {
  let zarr_file = new ZarrFile();
  let filename = ""
  if (file instanceof File) {
    filename = file.name;
    zarr_file.init(file).then(() => {
      zarr_file.ready = true;
    });
  }
  else if (typeof file == 'string') {
    file = standardize_path(file);
    filename = file;
    //make sure the filename doesn't end with '/'
    if (filename.endsWith('/')) {
      filename = filename.slice(0, -1);
    }
    zarr_file.init_from_url(file).then(() => {
      zarr_file.ready = true;
    });
  }
  else {
    throw new Error("'file' needs to be either a File object or a url!")
  }
  return {zarr_file_js: zarr_file, filename: filename};
}

export { ZarrFile, init_file };