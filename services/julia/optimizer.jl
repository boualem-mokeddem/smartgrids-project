using JuMP
using HiGHS
using HTTP
using JSON3

function is_pairwise_ok(sel, dist, R)
    for a in 1:length(sel), b in a+1:length(sel)
        if dist[sel[a]][sel[b]] > R
            return false
        end
    end
    return true
end

function totals(sel, buildings)
    C = sum(Float64(buildings[i]["cons"]) for i in sel)
    P = sum(Float64(buildings[i]["prod"]) for i in sel)
    return C, P
end

function has_prosumer(sel, buildings)
    any(Bool(buildings[i]["isProsumer"]) for i in sel)
end

function is_valid_grid(sel, buildings, dist, R, T)
    if length(sel) < 1
        return false
    end
    if !has_prosumer(sel, buildings)
        return false
    end
    if !is_pairwise_ok(sel, dist, R)
        return false
    end
    C, P = totals(sel, buildings)
    if C == 0.0
        return true
    end
    coverage = P / C
    return (coverage >= 1.0) || (coverage >= T)
end


function enumerate_valid_grids(data)
    buildings = data["buildings"]
    dist = data["distances"]
    N = Int(data["min_members"])
    R = Float64(data["max_radius"])
    T = Float64(data["coverage_threshold"])
    max_return = haskey(data, "max_return") ? Int(data["max_return"]) : 200

    n = length(buildings)
    results = []

    for mask in 1:(2^n - 1)
        sel = Int[]
        for i in 1:n
            if (mask >> (i-1)) & 1 == 1
                push!(sel, i)
            end
        end

        if length(sel) < N
            continue
        end

        if !is_valid_grid(sel, buildings, dist, R, T)
            continue
        end

        C, P = totals(sel, buildings)
        coverage = (C == 0.0) ? 0.0 : (P / C)

        waste = max(P - C, 0.0)
        deficit = max(C - P, 0.0)
        mismatch = waste + deficit  # = |P - C|

        push!(results, Dict(
            "building_ids" => [buildings[i]["id"] for i in sel],
            "size" => length(sel),
            "total_cons" => C,
            "total_prod" => P,
            "coverage_ratio" => coverage,
            "waste_kwh" => waste,
            "deficit_kwh" => deficit,
            "mismatch_kwh" => mismatch
        ))
    end

    # Sort by your business objective:
    # 1) minimal mismatch (closest balance)
    # 2) coverage closer to 1 (optional, helps avoid huge surplus)
    # 3) larger grids (optional)
    sort!(results, by = r -> (
        r["mismatch_kwh"],
        abs(r["coverage_ratio"] - 1.0),
        -r["size"]
    ))

    if length(results) > max_return
        results = results[1:max_return]
    end

    return Dict("status" => "ok", "count" => length(results), "grids" => results)
end


function solve_one_grid(buildings, dist, N, R, T)
    n = length(buildings)
    cons = [Float64(b["cons"]) for b in buildings]
    prod = [Float64(b["prod"]) for b in buildings]
    is_prosumer = [Bool(b["isProsumer"]) for b in buildings]

    model = Model(HiGHS.Optimizer)
    set_silent(model)

    @variable(model, x[1:n], Bin)

    @constraint(model, sum(x) >= N)
    @constraint(model, sum(x[i] for i in 1:n if is_prosumer[i]) >= 1)

    # Pairwise radius constraint: if dist(i,j) > R, cannot be together
    for i in 1:n, j in i+1:n
        if dist[i][j] > R
            @constraint(model, x[i] + x[j] <= 1)
        end
    end

    # Coverage constraint
    @constraint(model, sum(prod[i]*x[i] for i in 1:n) >= T * sum(cons[i]*x[i] for i in 1:n))

    # Objective (linear)
    @objective(model, Max, sum(prod[i]*x[i] for i in 1:n) - sum(cons[i]*x[i] for i in 1:n))

    optimize!(model)

    term = termination_status(model)
    if !(term in (OPTIMAL, FEASIBLE_POINT))
        return Dict("status" => string(term), "selected" => Int[])
    end

    sel = Int[]
    for i in 1:n
        if value(x[i]) > 0.5
            push!(sel, i)
        end
    end

    return Dict("status" => "ok", "selected" => sel)
end

function iterative_grids(data)
    buildings_all = data["buildings"]
    dist_all = data["distances"]
    N = Int(data["min_members"])
    R = Float64(data["max_radius"])
    T = Float64(data["coverage_threshold"])
    max_grids = haskey(data, "max_grids") ? Int(data["max_grids"]) : 10

    remaining = collect(1:length(buildings_all))
    grids = []

    while length(remaining) >= N && length(grids) < max_grids
        sub_buildings = [buildings_all[i] for i in remaining]
        k = length(remaining)
        sub_dist = [[Float64(dist_all[remaining[i]][remaining[j]]) for j in 1:k] for i in 1:k]

        res = solve_one_grid(sub_buildings, sub_dist, N, R, T)
        if res["status"] != "ok" || length(res["selected"]) < N
            break
        end

        chosen_local = res["selected"]
        chosen_global = [remaining[i] for i in chosen_local]
        chosen_ids = [buildings_all[i]["id"] for i in chosen_global]

        push!(grids, Dict("building_ids" => chosen_ids, "size" => length(chosen_ids)))

        chosen_set = Set(chosen_global)
        remaining = [i for i in remaining if !(i in chosen_set)]
    end

    return Dict("status" => "ok", "grids" => grids)
end

function handler(req)
    try
        if req.method != "POST"
            return HTTP.Response(405, "Use POST")
        end
        data = JSON3.read(String(req.body))
        result = enumerate_valid_grids(data)
        return HTTP.Response(200, JSON3.write(result))
    catch e
        return HTTP.Response(500, JSON3.write(Dict("status" => "error", "message" => sprint(showerror, e))))
    end
end

HTTP.serve(handler, "0.0.0.0", 8081)
