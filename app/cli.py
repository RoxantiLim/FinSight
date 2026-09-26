from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from app.analytics import TOOL_NAMES, execute_tool, get_tool_input_schema
from app.config import DEFAULT_DATABASE_PATH, DEFAULT_DEMO_OUTPUT_DIR, DEFAULT_DEMO_USER_ID
from app.db import create_db_engine, create_schema, create_session_factory
from app.demo.generator import DEFAULT_ANCHOR_DATE, DEFAULT_SEED, generate_demo_dataset
from app.importers import AlipayAdapter, WechatAdapter
from app.logging_config import configure_logging
from app.services.import_service import import_bill

LOGGER = logging.getLogger(__name__)


def _configure_console_encoding() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


def _date_value(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日期必须使用 YYYY-MM-DD 格式") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finsight", description="FinSight data and tools CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate-demo", help="生成可复现的演示账单")
    generate.add_argument("--output", type=Path, default=DEFAULT_DEMO_OUTPUT_DIR)
    generate.add_argument("--seed", type=int, default=DEFAULT_SEED)
    generate.add_argument("--anchor-date", type=_date_value, default=DEFAULT_ANCHOR_DATE)

    init_db = subparsers.add_parser("init-db", help="创建 SQLite 表")
    init_db.add_argument("--db", type=Path, default=DEFAULT_DATABASE_PATH)

    import_parser = subparsers.add_parser("import-bill", help="导入微信或支付宝账单")
    import_parser.add_argument("file", type=Path)
    import_parser.add_argument("--source", choices=("wechat", "alipay"), required=True)
    import_parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE_PATH)
    import_parser.add_argument("--user-id", default=DEFAULT_DEMO_USER_ID)

    setup = subparsers.add_parser("setup-demo", help="生成数据、建库并导入两种账单")
    setup.add_argument("--output", type=Path, default=DEFAULT_DEMO_OUTPUT_DIR)
    setup.add_argument("--db", type=Path, default=DEFAULT_DATABASE_PATH)
    setup.add_argument("--seed", type=int, default=DEFAULT_SEED)
    setup.add_argument("--anchor-date", type=_date_value, default=DEFAULT_ANCHOR_DATE)
    setup.add_argument("--user-id", default=DEFAULT_DEMO_USER_ID)

    run_tool = subparsers.add_parser("run-tool", help="独立执行一个只读分析工具")
    run_tool.add_argument("tool_name", choices=TOOL_NAMES)
    run_tool.add_argument("--arguments", default="{}", help="JSON object with tool arguments")
    run_tool.add_argument("--db", type=Path, default=DEFAULT_DATABASE_PATH)
    run_tool.add_argument("--user-id", default=DEFAULT_DEMO_USER_ID)

    tool_schema = subparsers.add_parser("tool-schema", help="输出分析工具输入 schema")
    tool_schema.add_argument("tool_name", choices=TOOL_NAMES)
    return parser


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _run_import(
    *, source: str, file_path: Path, database_path: Path, user_id: str
) -> dict[str, object]:
    adapters = {"wechat": WechatAdapter(), "alipay": AlipayAdapter()}
    engine = create_db_engine(database_path)
    create_schema(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        report = import_bill(
            session,
            adapter=adapters[source],
            file_path=file_path,
            user_id=user_id,
        )
    return report.model_dump(mode="json")


def main(argv: list[str] | None = None) -> int:
    _configure_console_encoding()
    configure_logging()
    args = build_parser().parse_args(argv)
    if args.command == "generate-demo":
        result = generate_demo_dataset(args.output, seed=args.seed, anchor_date=args.anchor_date)
        LOGGER.info("Demo dataset generated at %s", args.output.resolve())
        _print_json(result)
        return 0
    if args.command == "init-db":
        engine = create_db_engine(args.db)
        create_schema(engine)
        _print_json({"database": str(args.db.resolve()), "status": "ready"})
        return 0
    if args.command == "import-bill":
        _print_json(
            _run_import(
                source=args.source,
                file_path=args.file,
                database_path=args.db,
                user_id=args.user_id,
            )
        )
        return 0
    if args.command == "setup-demo":
        ground_truth = generate_demo_dataset(
            args.output, seed=args.seed, anchor_date=args.anchor_date
        )
        reports = [
            _run_import(
                source="wechat",
                file_path=args.output / "demo_wechat_bill.csv",
                database_path=args.db,
                user_id=args.user_id,
            ),
            _run_import(
                source="alipay",
                file_path=args.output / "demo_alipay_bill.csv",
                database_path=args.db,
                user_id=args.user_id,
            ),
        ]
        _print_json(
            {
                "database": str(args.db.resolve()),
                "generated_transactions": ground_truth["transaction_count"],
                "import_reports": reports,
            }
        )
        return 0
    if args.command == "run-tool":
        arguments = json.loads(args.arguments)
        if not isinstance(arguments, dict):
            raise ValueError("--arguments must be a JSON object")
        engine = create_db_engine(args.db)
        create_schema(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            result = execute_tool(
                session,
                user_id=args.user_id,
                tool_name=args.tool_name,
                arguments=arguments,
            )
        _print_json(result.model_dump(mode="json"))
        return 0
    if args.command == "tool-schema":
        _print_json(get_tool_input_schema(args.tool_name))
        return 0
    raise AssertionError(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
