def auto_generate_grids(
    db,
    center_building_id,
    radius_candidates,
    min_n,
    max_n,
    params_common,
    beam_width=20,
    max_grids=30,
):
    # radius_candidates contains center too, but we want selectable pool excluding center
    pool_ids = [r["building_id"] for r in radius_candidates if r["building_id"] != center_building_id]

    # state: list of partial grids (each is list of building_ids)
    beams = [[center_building_id]]
    found = []
    found_keys = set()

    # grow size from 2 to max_n
    for target_size in range(2, max_n + 1):
        candidates_next = []

        for grid_ids in beams:
            used = set(grid_ids)

            # heuristic: only try a limited subset of pool to avoid explosion
            # (optional) you can rank pool by distance or by being Prosumer
            for bid in pool_ids:
                if bid in used:
                    continue
                new_grid = grid_ids + [bid]

                eval_params = dict(params_common)
                eval_params["buildingIds"] = new_grid
                eval_params["N"] = int(min_n)  # overwrite N
                # Note: max_n is handled here, N is "min participants rule"

                res = db.query(Q.EVALUATE_GRID, eval_params)
                if not res:
                    continue
                r = res[0]

                candidates_next.append((new_grid, r))

        # sort candidates by score, keep best beam_width
        candidates_next.sort(key=lambda x: score_eval(x[1]), reverse=True)
        beams = [g for g, _ in candidates_next[:beam_width]]

        # collect valid grids if we’re >= min_n
        if target_size >= min_n:
            for g, r in candidates_next:
                if r["isValid"]:
                    key = grid_signature(g)
                    if key not in found_keys:
                        found_keys.add(key)
                        found.append({"building_ids": g, "eval": r})
                        if len(found) >= max_grids:
                            return found

    return found
