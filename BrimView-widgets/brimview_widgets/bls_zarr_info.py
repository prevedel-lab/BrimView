from dataclasses import dataclass, field, asdict
import json
from typing import Any, Dict, List, Optional

import panel as pn
from panel_jstree import Tree
import param
import pandas as pd

from panel.widgets.base import WidgetBase
from panel.custom import PyComponent

import brimfile as bls
from brimfile.validation.json_descriptor import generate_json_descriptor
from brimfile.file_abstraction import sync

import zarr


from .utils import catch_and_notify
from .logging import logger


class BlsZarrInfo(WidgetBase, PyComponent):
    """
    A widget to display the general file information.
    """

    value = param.ClassSelector(
        class_=bls.Data,
        default=None,
        allow_refs=True,
        doc="The names of the features selected and their set values",
    )

    def __init__(self, **params):
        self.title = pn.pane.Markdown("## Zarr file information")
        super().__init__(**params)

        # Tabulator to display zarr info
        self.info_tabulator = pn.widgets.Tabulator(
            show_index=False,
            disabled=True,
            visible=True,
            hidden_columns=["Group"],
        )

        # Tree to display the file structure
        self.tree = Tree(
            data=[],
            select_multiple=False,
            checkbox=False,
            plugins=["wholerow", "sort"],
            min_width=300,
            min_height=300,
        )
        # We bind the callback to the tree selection event, to update the details window when a node is selected
        # ( pn.depend(tree.value) is not working, so this is a quick workaround)
        pn.bind(self._tree_selected_callback, self.tree.param.value, watch=True)

        # Node details
        self.detail_text = pn.pane.Markdown("Click on a node to see its attributes")
        self.detail_tabulator = pn.widgets.Tabulator(
            show_index=False,
            disabled=True,
            visible=True,
            groupby=["Group"],
            hidden_columns=["Group"],
        )

        # Explicit annotation, because param and type hinting is not working properly
        self.value: bls.Data
        logger.info("BlsZarrInfo initialized")

    @catch_and_notify(prefix="<b>Zarr detail display: </b>")
    def _tree_selected_callback(self, selected_ids):
        logger.info(
            f"Zarr tree file selected node changed, selected_ids: {selected_ids}"
        )
        selected_id = selected_ids[0] if selected_ids else None
        node = None
        for n in self.tree.flat_tree:
            if n["id"] == selected_id:
                node = n
                break

        logger.debug(f"Selected node: {node}")
        if node is None:
            self.detail_tabulator.value = None
            self.detail_text.object = "Click on a node to see its attributes"
            return
        dataframe = dict_to_tabulator_df(node["data"])
        self.detail_tabulator.value = dataframe
        self.detail_text.object = (
            f"Clicked on node: _{node['text']}_."  # TODO: Display the absolute path
        )

    @param.depends("value")
    @catch_and_notify(prefix="<b>Zarr file size: </b>")
    def _size_widget(self):
        logger.info("Calculating Zarr file size")
        if self.value is None:
            return pn.pane.Markdown("")

        store = self.value._file._store
        size = sync(store.getsize_prefix("/"))
        readable_size = bytes_human_string(size)
        msg = f"Reported file size: **{readable_size}** (= {size} bytes)"
        logger.debug(msg)
        return pn.pane.Markdown(msg)

    @param.depends("value", watch=True)
    @catch_and_notify(prefix="<b>Zarr file info: </b>")
    def _info_widget(self):
        if self.value is None:
            self.info_tabulator.value = None
            return
        root: zarr.Group = self.value._file._root
        group_info = sync(root.info_complete())
        # group info is a dataclass
        self.info_tabulator.value = dict_to_tabulator_df(asdict(group_info))

    @param.depends("value", watch=True)
    @catch_and_notify(prefix="<b>Update Zarr tree: </b>")
    def _update_tree_widget(self):
        if self.value is None:
            self.tree.data = []
            return
        file = self.value._file

        logger.debug("Retrieving json descriptor for the Zarr file")
        json_tree = generate_json_descriptor(file)
        logger.info("Converting json_descriptor to jstree format")
        tree = json.loads(json_tree)
        typed_tree = brimfilejson_to_jstree(tree)
        for root_node in typed_tree:
            root_node.state = NodeState(opened=True)
        dict_tree = [asdict(node) for node in typed_tree]

        logger.debug("Updating tree widget with new data")
        self.tree.data = dict_tree

    def __panel__(self):
        return pn.Column(
            self.title,
            pn.Row(
                self.info_tabulator,
                self._size_widget,
            ),
            pn.layout.Divider(height=10, margin=10),
            pn.FlexBox(
                pn.Column("### Zarr file tree", self.tree),
                pn.Column(self.detail_text, "### Attributes", self.detail_tabulator),
                sizing_mode="scale_width",
            ),
        )


### === Helper class, functions and other utils ===
@dataclass
class NodeState:
    opened: bool = False
    selected: bool = False
    disabled: bool = False


@dataclass
class TreeNode:
    """
    A class representing a node in the tree structure.
    Expected to be consumed by the panel_jstree component: you first need to convert
    it into a dictionary with `asdict()` before passing to the Tree widget.

    Contains some shared code between different node type.
    """

    text: str
    state: Optional[NodeState] = None
    icon: Optional[str] = None
    children: List["TreeNode"] = field(default_factory=list)

    data: Dict[str, Any] = field(
        default_factory=dict
    )  # Allows to store arbitrary  data in the node, that can be used for the details dialog for example
    node_type: str = "group"  # or "array"

    def __post_init__(self):
        icon_by_node_type = {
            "group": "jstree-folder",
            "array": "jstree-file",
        }
        if self.icon is None:
            self.icon = icon_by_node_type.get(self.node_type, "folder")


def brimfilejson_to_jstree(brimfile_descriptor) -> List[TreeNode]:
    """
    Convert the json_descriptor generated by brimfile into a list of TreeNode,
    that can be processed by panel_jstree
    """

    def convert_node(node: dict, node_name) -> Optional[TreeNode]:
        attributes: dict = node["attributes"]
        node_type: str = node["node_type"]
        if node_type == "group":
            children = node.keys() - {"attributes", "node_type"}
            return TreeNode(
                text=node_name,
                children=[convert_node(node[child], child) for child in children],
                data=attributes,
                node_type=node_type,
            )
        elif node_type == "array":
            # For better readability, we convert the shape into a human readable string
            # We want : "[512, 512]" instead of "512,512"
            human_pretty_shape = f"[ {', '.join(map(str, node['shape']))} ]"
            attributes.update({"shape": human_pretty_shape, "dtype": node["dtype"]})
            return TreeNode(
                text=node_name,
                data=attributes,
                node_type=node_type,
            )
        else:
            return None

    children = brimfile_descriptor.keys() - {"attributes", "node_type"}
    return [convert_node(brimfile_descriptor[key], key) for key in children]


def dict_to_tabulator_df(data: dict) -> pd.DataFrame:
    rows = []

    def walk(d, path=None):
        path = path or []

        for key, value in d.items():
            if isinstance(value, dict):
                walk(value, path + [key])
            else:
                rows.append(
                    {
                        "Group": "/".join(path) if path else None,
                        "Name": key,
                        "Value": value,
                    }
                )

    walk(data)

    return pd.DataFrame(rows)


def bytes_human_string(num_bytes):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num_bytes < 1024.0:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} PB"
