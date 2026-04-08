from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from scripts.batch_api_spec import api_id_to_filename
from scripts.docapi_cli import default_output_json, resolve_output_root
from scripts.generate_api_spec import build_default_output_name


def test_generate_api_spec_uses_reference_style_name() -> None:
    config = {
        "cover": {
            "spec_no": "22",
            "feature_name": "社員台帳",
            "api_id": "syainDaicho/show",
            "api_name": "社員台帳を表示する",
        },
        "api_info": {
            "url": "/api/syainDaicho/show",
        },
    }

    output_name = build_default_output_name(config)
    assert output_name.endswith("output\\22.API設計書-社員台帳-表示.xlsx")


def test_batch_api_spec_uses_reference_style_name() -> None:
    filename = api_id_to_filename(
        Path("showCompany.json"),
        {
            "cover": {
                "spec_no": "22",
                "feature_name": "社員台帳",
                "api_id": "syainDaicho/update",
                "api_name": "社員台帳を更新する",
            }
        },
    )

    assert filename == "22.API設計書-社員台帳-更新.xlsx"


def test_docapi_defaults_to_output_directory() -> None:
    args = Namespace(output_json=None, output_dir=None)
    assert default_output_json(args).name == "scan.json"
    assert default_output_json(args).parent.name == "output"
    assert resolve_output_root(args).name == "output"
