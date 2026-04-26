#!/usr/bin/env python3
"""
========================================================
CyberSuite — Publisher Core (L,L,L Complete)
========================================================
Supports:
- DOCS mode  → one file per record
- INDEX mode → single aggregate file

Responsibilities:
- validate inputs
- normalize data
- render templates via Jinja
- write output (guarded by dry_run / overwrite)

No UI assumptions.
No global state.
No implicit side effects.
========================================================
"""

import os
import jinja2

from publisher_core.errors import PublisherError
from publisher_core.results import RenderResult
from pypub_utils.input_matrix import parse_data_file


class PublisherCore:
    """
    Publisher execution core.
    """

    def __init__(
        self,
        template_path: str,
        data_path: str,
        output_path: str,
        output_ext: str = ".html",
        dry_run: bool = False,
        mode: str = "docs",
        overwrite: bool = False,
        publish_target: str = "dev",
    ):
        self.template_path = template_path
        self.data_path = data_path
        self.output_path = output_path

        # normalize extension
        self.output_ext = output_ext if output_ext.startswith(".") else f".{output_ext}"

        self.dry_run = dry_run
        self.mode = mode
        self.overwrite = overwrite
        self.publish_target = publish_target

        self.logs: list[str] = []
        self.errors: list[str] = []

    # ----------------------------------------------------
    # HELPER() FOR THE INCLUSION OFTRACKING ON PROD
    # ----------------------------------------------------
    def _read_text_file(self, path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()

    def _get_tracking_snippets(self) -> tuple[str, str]:
        """
        Return (tracking_head, tracking_footer) for the current publish_target.

        dev  → ("", "")  — no tracking injected
        stage / prod → load from instance tracking/ directory
        """
        if self.publish_target == "dev":
            return "", ""

        from pypub.instance_manager import get_active_instance_root
        tracking_dir = get_active_instance_root() / "common_policy" / "tracking"

        if self.publish_target == "stage":
            return (
                self._read_text_file(tracking_dir / "stage_head.html"),
                self._read_text_file(tracking_dir / "stage_footer.html"),
            )

        if self.publish_target == "prod":
            return (
                self._read_text_file(tracking_dir / "prod_head.html"),
                self._read_text_file(tracking_dir / "prod_footer.html"),
            )

        raise PublisherError(
            f"Unknown publish target: {self.publish_target}\n"
            f"   Expected one of: dev, stage, prod"
        )

    # ----------------------------------------------------
    # Main Run
    # ----------------------------------------------------
    def run(self) -> RenderResult:
        try:
            # ------------------------------------------------
            # Phase 0: ensure output directory
            # ------------------------------------------------
            if self.dry_run:
                self.logs.append(
                    f"[dry-run] Would ensure output dir: {self.output_path}"
                )
            else:
                os.makedirs(self.output_path, exist_ok=True)
                self.logs.append(f"Ensured output dir: {self.output_path}")

            # ------------------------------------------------
            # Phase 0.5: template existence validation
            # ------------------------------------------------
            if not os.path.exists(self.template_path):
                raise PublisherError(
                    f"Template not found: {self.template_path}\n"
                    f"   Check path, then retry"
                )

            # ------------------------------------------------
            # Phase 0.6: data file existence validation
            # ------------------------------------------------
            if not os.path.exists(self.data_path):
                raise PublisherError(
                    f"Data file not found: {self.data_path}\n"
                    f"   Supported formats: .json, .xml, .csv, .md"
                )

            # ------------------------------------------------
            # Phase 1: normalize input data
            # ------------------------------------------------
            data = parse_data_file(self.data_path)

            records = data.get("records", [])
            meta = data.get("meta", {})

            if not records:
                found_keys = list(data.keys())
                raise PublisherError(
                    f"No records found in data file: {self.data_path}\n"
                    f"   Found keys: {found_keys}\n"
                    f'   Expected: {{"records": [...]}}'
                )

            self.logs.append(
                f"Normalized data: {len(records)} records, {len(meta)} meta fields"
            )

            tracking_head, tracking_footer = self._get_tracking_snippets()

            # ------------------------------------------------
            # Phase 2: render output
            # ------------------------------------------------
            with open(self.template_path, "r", encoding="utf-8") as f:
                template_src = f.read()

            template = jinja2.Template(template_src)

            written = []

            # ================================
            # DOCS mode — one file per record
            # ================================
            if self.mode == "docs":
                for i, record in enumerate(records, 1):
                    safe_name = record.get("filename", f"record_{i}").strip()

                    out_file = os.path.join(
                        self.output_path,
                        f"{safe_name}{self.output_ext}",
                    )

                    if not self.overwrite and os.path.exists(out_file):
                        raise PublisherError(
                            f"File already exists: {out_file}\n"
                            f"   Add --overwrite to replace existing files"
                        )

                    try:
                        rendered = template.render(
                            record=record,
                            records=records,
                            meta=meta,
                            output_ext=self.output_ext,
                            publish_target=self.publish_target,
                            tracking_head=tracking_head,
                            tracking_footer=tracking_footer,
                        )
                    except jinja2.exceptions.UndefinedError as e:
                        raise PublisherError(
                            f"Template references undefined variable: {e}\n"
                            f"   Available: record.*, records, meta.*, output_ext"
                        )
                    except jinja2.exceptions.TemplateError as e:
                        raise PublisherError(f"Template syntax error: {e}")

                    if self.dry_run:
                        self.logs.append(f"[dry-run] Would write file: {out_file}")
                        written.append(out_file)
                    else:
                        with open(out_file, "w", encoding="utf-8") as f:
                            f.write(rendered)
                        self.logs.append(f"Wrote file: {out_file}")
                        written.append(out_file)
            # ================================
            # INDEX mode — single aggregate file
            # ================================
            elif self.mode == "index":
                out_file = os.path.join(
                    self.output_path,
                    f"{meta.get('filename', 'index')}{self.output_ext}",
                )

                if not self.overwrite and os.path.exists(out_file):
                    raise PublisherError(
                        f"File already exists: {out_file}\n"
                        f"   Add --overwrite to replace existing files"
                    )

                try:
                    rendered = template.render(
                        records=records,
                        meta=meta,
                        output_ext=self.output_ext,
                        publish_target=self.publish_target,
                        tracking_head=tracking_head,
                        tracking_footer=tracking_footer,
                    )
                except jinja2.exceptions.UndefinedError as e:
                    raise PublisherError(
                        f"Template references undefined variable: {e}\n"
                        f"   Available: records, meta.*, output_ext"
                    )
                except jinja2.exceptions.TemplateError as e:
                    raise PublisherError(f"Template syntax error: {e}")

                if self.dry_run:
                    self.logs.append(f"[dry-run] Would write file: {out_file}")
                    written.append(out_file)
                else:
                    with open(out_file, "w", encoding="utf-8") as f:
                        f.write(rendered)
                    self.logs.append(f"Wrote file: {out_file}")
                    written.append(out_file)

            else:
                raise PublisherError(f"Unknown publish mode: {self.mode}")

            # ------------------------------------------------
            # Done
            # ------------------------------------------------
            return RenderResult(
                success=True,
                logs=self.logs,
                errors=self.errors,
                files=written,
            )

        except Exception as e:
            self.errors.append(str(e))
            return RenderResult(
                success=False,
                logs=self.logs,
                errors=self.errors,
                files=[],
            )
