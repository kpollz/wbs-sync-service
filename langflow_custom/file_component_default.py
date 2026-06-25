from copy import deepcopy
from typing import Any

from langflow.base.data.base_file import BaseFileComponent
from langflow.base.data.utils import TEXT_FILE_TYPES, parallel_load_data, parse_text_file_to_data
from langflow.io import BoolInput, FileInput, IntInput, Output
from langflow.schema.data import Data
from langflow.utils.constants import AGENT_BUILDER_GUIDE_URL


class FileComponent(BaseFileComponent):
    """Handles loading and processing of individual or zipped text files.

    This component supports processing multiple valid files within a zip archive,
    resolving paths, validating file types, and optionally using multithreading for processing.
    """

    display_name = "File"
    description = "Loads content from one or more files as a DataFrame."
    documentation = f'{AGENT_BUILDER_GUIDE_URL}/components/en_components-io/#file'
    icon = "file-text"
    name = "File"
    admin = False

    VALID_EXTENSIONS = TEXT_FILE_TYPES

    _base_inputs = deepcopy(BaseFileComponent._base_inputs)

    for input_item in _base_inputs:
        if isinstance(input_item, FileInput) and input_item.name == "path":
            input_item.real_time_refresh = True
            break

    inputs = [
        *_base_inputs,
        BoolInput(
            name="use_multithreading",
            display_name="[Deprecated] Use Multithreading",
            advanced=True,
            value=True,
            info="Set 'Processing Concurrency' greater than 1 to enable multithreading.",
        ),
        IntInput(
            name="concurrency_multithreading",
            display_name="Processing Concurrency",
            advanced=True,
            info="When multiple files are being processed, the number of files to process concurrently.",
            value=1,
        ),
    ]

    outputs = [
        Output(display_name="Raw Content", name="message", method="load_files_message"),
    ]

    # TODO: Remove this if abstract method process_files is removed.
    def process_files(self, file_list: list[BaseFileComponent.BaseFile]) -> list[BaseFileComponent.BaseFile]:
        raise NotImplementedError("Avoid not implemented error for abstract method for test")

    def update_outputs(self, frontend_node: dict, field_name: str, field_value: Any) -> dict:
        """Dynamically show only the relevant output based on the number of files processed."""
        if field_name == "path":
            # Add outputs based on the number of files in the path
            if len(field_value) == 0:
                return frontend_node

            frontend_node["outputs"] = []

            if len(field_value) == 1:
                # We need to check if the file is structured content
                file_path = frontend_node["template"]["path"]["file_path"][0].lower()
                if file_path.endswith((".csv", ".xlsx", ".parquet", ".json")):
                    frontend_node["outputs"].append(
                        Output(display_name="Structured Content", name="dataframe", method="load_files_structured"),
                    )

                if file_path.endswith(".json"):
                    frontend_node["outputs"].append(
                        Output(display_name="JSON Content", name="json", method="load_files_json"),
                    )

                if file_path.endswith(".yml") or file_path.endswith(".yaml"):
                    frontend_node["outputs"].append(
                        Output(display_name="YAML Content", name="yaml", method="load_files_yaml"),
                    )

                # All files get the raw content and path outputs
                frontend_node["outputs"].append(
                    Output(display_name="Raw Content", name="message", method="load_files_message"),
                )
            else:
                # For multiple files, we only show the files output
                frontend_node["outputs"].append(
                    Output(display_name="Files", name="dataframe", method="load_files"),
                )

                file_paths = frontend_node["template"]["path"]["file_path"]
                file_exts = set(file_path.split(".")[-1].lower() for file_path in file_paths)
                if not (file_exts - {"csv", "xlsx", "parquet", "json"}):
                    frontend_node["outputs"].append(
                        Output(display_name="Structured Contents", name="dataframe_contents", method="load_files_structured"),
                    )

        return frontend_node