"""从检查点目录加载进化树/特征地图数据（纯模块，不 import visualizer / backend.core / backend.app）。

这是上游 visualizer 的 ``load_evolution_data`` / ``sanitize_program_for_visualization`` /
``check_json_float`` 原样抽出，外加团队增强的 ``_build_feature_maps``（MAP-Elites 热力图）。
visualizer.py 会 re-export 这些名字以保持上游测试与旧入口兼容。
"""

from __future__ import annotations

import json
import math
import os
import logging
from numbers import Number
from typing import Any, Optional

logger = logging.getLogger(__name__)


def load_evolution_data(checkpoint_folder):
    meta_path = os.path.join(checkpoint_folder, "metadata.json")
    programs_dir = os.path.join(checkpoint_folder, "programs")
    if not os.path.exists(meta_path) or not os.path.exists(programs_dir):
        logger.info(f"Missing metadata.json or programs dir in {checkpoint_folder}")
        return {
            "archive": [],
            "nodes": [],
            "edges": [],
            "checkpoint_dir": checkpoint_folder,
            "feature_maps": [],
            "feature_stats": {},
        }
    with open(meta_path) as f:
        meta = json.load(f)

    nodes = []
    id_to_program = {}
    pids = set()
    for island_idx, id_list in enumerate(meta.get("islands", [])):
        for pid in id_list:
            prog_path = os.path.join(programs_dir, f"{pid}.json")

            # Keep track of PIDs and if one is double, append "-copyN" to the PID
            if pid in pids:
                base_pid = pid

                # If base_pid already has a "-copyN" suffix, strip it
                if "-copy" in base_pid:
                    base_pid = base_pid.rsplit("-copy", 1)[0]

                # Find the next available copy number
                copy_num = 1
                while f"{base_pid}-copy{copy_num}" in pids:
                    copy_num += 1
                pid = f"{base_pid}-copy{copy_num}"
            pids.add(pid)

            if os.path.exists(prog_path):
                with open(prog_path) as pf:
                    prog = json.load(pf)
                    sanitize_program_for_visualization(prog)
                # Embedding vectors are large and never rendered; drop them.
                prog.pop("embedding", None)
                prog["id"] = pid
                prog["island"] = island_idx
                nodes.append(prog)
                id_to_program[pid] = prog
            else:
                logger.debug(f"Program file not found: {prog_path}")

    edges = []
    for prog in nodes:
        parent_id = prog.get("parent_id")
        if parent_id and parent_id in id_to_program:
            edges.append({"source": parent_id, "target": prog["id"]})

    logger.info(f"Loaded {len(nodes)} nodes and {len(edges)} edges from {checkpoint_folder}")
    return {
        "archive": meta.get("archive", []),
        "nodes": nodes,
        "edges": edges,
        "checkpoint_dir": checkpoint_folder,
        "feature_maps": _build_feature_maps(meta, id_to_program),
        "feature_stats": meta.get("feature_stats", {}) or {},
    }


def _build_feature_maps(meta: dict, programs: dict) -> list[dict[str, Any]]:
    """Turn the saved MAP-Elites grids into per-island cells for the heat map."""
    stats = meta.get("feature_stats") or {}
    dimensions = [str(name) for name in stats.keys()] if isinstance(stats, dict) else []
    if len(dimensions) < 2:
        default = ["complexity", "diversity"]
        for name in default:
            if name not in dimensions:
                dimensions.append(name)

    feature_maps = []
    for island_idx, grid in enumerate(meta.get("island_feature_maps", []) or []):
        cells = []
        max_x = 0
        max_y = 0
        for key, program_id in (grid or {}).items():
            try:
                bins = [int(part) for part in str(key).split("-")]
            except ValueError:
                continue
            if len(bins) < 2:
                continue
            program = programs.get(program_id) or {}
            fitness = (program.get("metrics") or {}).get("combined_score")
            cells.append(
                {
                    "x": bins[0],
                    "y": bins[1],
                    "program_id": program_id,
                    "fitness": fitness if check_json_float(fitness) else None,
                }
            )
            max_x = max(max_x, bins[0])
            max_y = max(max_y, bins[1])
        feature_maps.append(
            {
                "island": island_idx,
                "dimensions": dimensions[:2],
                "bins_x": max_x + 1 if cells else 0,
                "bins_y": max_y + 1 if cells else 0,
                "cells": cells,
            }
        )
    return feature_maps


def sanitize_program_for_visualization(program: dict[str, Any]) -> None:
    for k, v in program["metrics"].items():
        if not check_json_float(v):
            program["metrics"][k] = None
        if "parent_metrics" in program["metadata"]:
            for k, v in program["metadata"]["parent_metrics"].items():
                if not check_json_float(v):
                    program["metadata"]["parent_metrics"][k] = None


def check_json_float(v: Optional[float]) -> bool:
    return isinstance(v, Number) and not (math.isinf(v) or math.isnan(v))
