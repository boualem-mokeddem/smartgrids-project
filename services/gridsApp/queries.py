# queries.py

COMMUNES = """
MATCH (c:Commune)
RETURN c.code AS code, c.nom AS name
ORDER BY name;
"""

IRIS_BY_COMMUNE = """
MATCH (i:IRIS)-[:SITUATED_IN]->(c:Commune {code:$communeCode})
RETURN i.code AS code, i.nom AS name
ORDER BY name;
"""

BUILDINGS_IN_IRIS = """
MATCH (i:IRIS {code:$irisCode})<-[:IN_IRIS]-(b)
WHERE b.location IS NOT NULL
RETURN b.building_id AS building_id,
       labels(b) AS labels,
       coalesce(b.has_pv,false) AS has_pv,
       coalesce(b.pv_capacity_kwp,0.0) AS pv_kwp,
       b.iris_code AS iris_code,
       b.location.latitude AS lat,
       b.location.longitude AS lon
ORDER BY building_id
LIMIT 5000;
"""

BUILDINGS_ENERGY_SUMMARY = """
UNWIND $buildingIds AS bid
MATCH (b {building_id: bid})
OPTIONAL MATCH (b)-[c:CONSUMED_ON]->(d:Day)
WHERE d.date >= date($startDate) AND d.date <= date($endDate)
WITH b, sum(coalesce(c.consumption_kwh, 0.0)) AS cons
OPTIONAL MATCH (b)-[p:PRODUCED_ON]->(d2:Day)
WHERE d2.date >= date($startDate) AND d2.date <= date($endDate)
RETURN
  b.building_id AS building_id,
  labels(b) AS labels,
  coalesce(cons, 0.0) AS cons,
  sum(coalesce(p.production_kwh, 0.0)) AS prod,
  b.location.latitude AS lat,
  b.location.longitude AS lon;
"""



BUILDINGS_WITHIN_RADIUS = """
MATCH (center {building_id:$centerBuildingId})
WITH center, center.location AS cpt
MATCH (b)
WHERE b.location IS NOT NULL
  AND point.distance(b.location, cpt) <= $radiusMeters
RETURN 
    b.building_id AS building_id,
    labels(b) AS labels,
    point.distance(b.location, cpt) AS dist_m,
    b.iris_code AS iris_code,
    b.location.x AS lon,
    b.location.y AS lat
ORDER BY dist_m
LIMIT 5000;
"""

PROVIDERS_LIST = """
MATCH (p:Provider)
RETURN
  p.provider_id AS id,
  p.name AS name,
  p.type AS type,
  p.price_eur_per_kwh AS price,
  p.buy_eur_per_kwh AS buy_eur_per_kwh
ORDER BY price ASC;
"""

CREATE_GRAPH_DB_STEPS = [
    # contraintes
    """CREATE CONSTRAINT building_id_unique IF NOT EXISTS
    FOR (b:Building) REQUIRE b.building_id IS UNIQUE""",

    """CREATE CONSTRAINT day_date_unique IF NOT EXISTS
    FOR (d:Day) REQUIRE d.date IS UNIQUE""",

    """CREATE CONSTRAINT iris_code_unique IF NOT EXISTS
    FOR (i:IRIS) REQUIRE i.code IS UNIQUE""",

    """CREATE CONSTRAINT commune_code_unique IF NOT EXISTS
    FOR (c:Commune) REQUIRE c.code IS UNIQUE""",

    """CREATE CONSTRAINT epci_code_unique IF NOT EXISTS
    FOR (e:EPCI) REQUIRE e.code IS UNIQUE""",

    """CREATE CONSTRAINT departement_code_unique IF NOT EXISTS
    FOR (d:Departement) REQUIRE d.code IS UNIQUE""",

    """CREATE CONSTRAINT region_code_unique IF NOT EXISTS
    FOR (r:Region) REQUIRE r.code IS UNIQUE""",

    """CREATE CONSTRAINT btline_id_unique IF NOT EXISTS
    FOR (l:BT_Line) REQUIRE l.btline_id IS UNIQUE""",

    # indexes
    """CREATE INDEX building_iris_idx IF NOT EXISTS
    FOR (b:Building) ON (b.iris_code)""",

    """CREATE INDEX provider_id IF NOT EXISTS
    FOR (p:Provider) ON (p.provider_id)""",

    """CREATE INDEX building_sell_price IF NOT EXISTS
    FOR (b:Building) ON (b.sell_price_eur_per_kwh)""",

    """CREATE POINT INDEX building_location IF NOT EXISTS
    FOR (b:Building) ON (b.location)""",

    """CREATE POINT INDEX prosumer_location IF NOT EXISTS
    FOR (b:Prosumer) ON (b.location)""",

    """CREATE POINT INDEX consumer_location IF NOT EXISTS
    FOR (b:Consumer) ON (b.location)""",

    """CREATE POINT INDEX btline_centroid_idx IF NOT EXISTS
    FOR (l:BT_Line) ON (l.centroid)""",

    """CREATE POINT INDEX provider_location IF NOT EXISTS
    FOR (p:Provider) ON (p.location)""",

    # load buildings
    """
    LOAD CSV WITH HEADERS FROM 'file:///buildings_enriched_with_iris.csv' AS row
    CALL {
      WITH row
      WITH
        row,
        trim(row.building_id) AS bid,
        CASE
          WHEN row.iris_code IS NULL OR trim(row.iris_code) = '' THEN NULL
          ELSE toString(toInteger(toFloat(row.iris_code)))
        END AS irisKey,
        CASE
          WHEN toLower(trim(row.has_pv)) IN ['true','1','yes'] THEN true
          ELSE false
        END AS hasPv,
        CASE WHEN row.lon IS NULL OR trim(row.lon) = '' THEN NULL ELSE toFloat(row.lon) END AS lon,
        CASE WHEN row.lat IS NULL OR trim(row.lat) = '' THEN NULL ELSE toFloat(row.lat) END AS lat,
        CASE
          WHEN row.pv_capacity_kwp IS NULL OR trim(row.pv_capacity_kwp) = '' THEN NULL
          ELSE toFloat(row.pv_capacity_kwp)
        END AS pvKwp
      WHERE bid IS NOT NULL AND bid <> ''
      MERGE (b:Building {building_id: bid})
      SET
        b.iris_code = irisKey,
        b.nom_iris = row.nom_iris,
        b.has_pv = hasPv,
        b.pv_capacity_kwp = pvKwp,
        b.location =
          CASE
            WHEN lon IS NULL OR lat IS NULL THEN NULL
            ELSE point({longitude: lon, latitude: lat, srid: 4326})
          END,
        b.code_insee = CASE WHEN row.code_insee IS NULL OR trim(row.code_insee)='' THEN NULL ELSE trim(row.code_insee) END,
        b.nom_commune = row.nom_commune,
        b.code_postal = row.code_postal,
        b.nom_voie = row.nom_voie,
        b.numero = row.numero,
        b.type_position = row.type_position
    } IN TRANSACTIONS OF 20000 ROWS
    """,

    # IRIS + relation IN_IRIS
    """
    LOAD CSV WITH HEADERS FROM 'file:///buildings_enriched_with_iris.csv' AS row
    CALL {
      WITH row
      WITH
        trim(row.building_id) AS bid,
        CASE
          WHEN row.iris_code IS NULL OR trim(row.iris_code) = '' THEN NULL
          ELSE toString(toInteger(toFloat(row.iris_code)))
        END AS irisKey,
        row.nom_iris AS irisName
      WHERE bid IS NOT NULL AND bid <> '' AND irisKey IS NOT NULL
      MERGE (i:IRIS {code: irisKey})
      SET i.nom = coalesce(i.nom, irisName)
      WITH i, bid
      MATCH (b:Building {building_id: bid})
      MERGE (b)-[:IN_IRIS]->(i)
    } IN TRANSACTIONS OF 20000 ROWS
    """,

    # IRIS -> Commune
    """
    LOAD CSV WITH HEADERS FROM 'file:///buildings_enriched_with_iris.csv' AS row
    CALL {
      WITH row
      WITH
        CASE
          WHEN row.iris_code IS NULL OR trim(row.iris_code) = '' THEN NULL
          ELSE toString(toInteger(toFloat(row.iris_code)))
        END AS irisKey,
        CASE
          WHEN row.code_insee IS NULL OR trim(row.code_insee) = '' THEN NULL
          ELSE trim(row.code_insee)
        END AS communeCode,
        row.nom_iris AS irisName,
        row.nom_commune AS communeName
      WHERE irisKey IS NOT NULL AND communeCode IS NOT NULL
      MERGE (i:IRIS {code: irisKey})
      SET i.nom = coalesce(i.nom, irisName)
      MERGE (c:Commune {code: communeCode})
      SET c.nom = coalesce(c.nom, communeName)
      MERGE (i)-[:SITUATED_IN]->(c)
    } IN TRANSACTIONS OF 20000 ROWS
    """,

    # hierarchy
    """
    LOAD CSV WITH HEADERS FROM 'file:///reseau-souterrain-bt-neo4j.csv' AS row
    CALL {
      WITH row
      WITH
        trim(row.code_region) AS regionCode,
        row.nom_region AS regionName,
        trim(row.code_departement) AS depCode,
        row.nom_departement AS depName,
        trim(row.code_epci) AS epciCode,
        row.nom_epci AS epciName,
        trim(row.code_commune) AS communeCode,
        row.nom_commune AS communeName
      WHERE regionCode IS NOT NULL AND regionCode <> ''
        AND depCode IS NOT NULL AND depCode <> ''
        AND epciCode IS NOT NULL AND epciCode <> ''
        AND communeCode IS NOT NULL AND communeCode <> ''
      MERGE (r:Region {code: regionCode})
      SET r.nom = coalesce(r.nom, regionName)
      MERGE (d:Departement {code: depCode})
      SET d.nom = coalesce(d.nom, depName)
      MERGE (d)-[:SITUATED_IN]->(r)
      MERGE (e:EPCI {code: epciCode})
      SET e.nom = coalesce(e.nom, epciName)
      MERGE (e)-[:SITUATED_IN]->(d)
      MERGE (c:Commune {code: communeCode})
      SET c.nom = coalesce(c.nom, communeName)
      MERGE (c)-[:SITUATED_IN]->(e)
    } IN TRANSACTIONS OF 50000 ROWS
    """,

    # BT lines
    """
    LOAD CSV WITH HEADERS FROM 'file:///reseau-souterrain-bt-neo4j.csv' AS row
    CALL {
      WITH row
      WITH
        row,
        CASE
          WHEN row.code_iris IS NULL OR trim(row.code_iris) = '' THEN NULL
          ELSE toString(toInteger(toFloat(row.code_iris)))
        END AS irisKey,
        row.wkt AS wkt,
        CASE WHEN row.centroid_lon IS NULL OR trim(row.centroid_lon)='' THEN NULL ELSE toFloat(row.centroid_lon) END AS clon0,
        CASE WHEN row.centroid_lat IS NULL OR trim(row.centroid_lat)='' THEN NULL ELSE toFloat(row.centroid_lat) END AS clat0
      WITH
        row, irisKey, wkt,
        CASE WHEN clon0 IS NULL THEN NULL ELSE round(clon0 * 1000000.0) / 1000000.0 END AS clon,
        CASE WHEN clat0 IS NULL THEN NULL ELSE round(clat0 * 1000000.0) / 1000000.0 END AS clat
      WITH
        row, irisKey, wkt, clon, clat,
        replace(
          coalesce(irisKey,'NA') + '_' + coalesce(wkt,'') + '_' + toString(clon) + '_' + toString(clat),
          ' ',
          ''
        ) AS btId
      MERGE (l:BT_Line {btline_id: btId})
      SET
        l.wkt = wkt,
        l.srid = 4326,
        l.centroid = CASE
          WHEN clon IS NULL OR clat IS NULL THEN NULL
          ELSE point({longitude: clon, latitude: clat, srid: 4326})
        END,
        l.code_commune = row.code_commune,
        l.nom_commune = row.nom_commune,
        l.code_epci = row.code_epci,
        l.nom_epci = row.nom_epci,
        l.code_departement = row.code_departement,
        l.nom_departement = row.nom_departement,
        l.code_region = row.code_region,
        l.nom_region = row.nom_region,
        l.code_iris = irisKey,
        l.nom_iris = row.nom_iris
    } IN TRANSACTIONS OF 50000 ROWS
    """,

    # BT_Line -> IRIS
    """
    LOAD CSV WITH HEADERS FROM 'file:///reseau-souterrain-bt-neo4j.csv' AS row
    CALL {
      WITH row
      WITH
        row,
        CASE
          WHEN row.code_iris IS NULL OR trim(row.code_iris) = '' THEN NULL
          ELSE toString(toInteger(toFloat(row.code_iris)))
        END AS irisKey,
        row.nom_iris AS irisName,
        row.wkt AS wkt,
        CASE WHEN row.centroid_lon IS NULL OR trim(row.centroid_lon)='' THEN NULL ELSE toFloat(row.centroid_lon) END AS clon0,
        CASE WHEN row.centroid_lat IS NULL OR trim(row.centroid_lat)='' THEN NULL ELSE toFloat(row.centroid_lat) END AS clat0
      WHERE irisKey IS NOT NULL
      WITH
        row, irisKey, irisName, wkt,
        CASE WHEN clon0 IS NULL THEN NULL ELSE round(clon0 * 1000000.0) / 1000000.0 END AS clon,
        CASE WHEN clat0 IS NULL THEN NULL ELSE round(clat0 * 1000000.0) / 1000000.0 END AS clat
      WITH
        irisKey, irisName,
        replace(
          coalesce(irisKey,'NA') + '_' + coalesce(wkt,'') + '_' + toString(clon) + '_' + toString(clat),
          ' ',
          ''
        ) AS btId
      MERGE (i:IRIS {code: irisKey})
      SET i.nom = coalesce(i.nom, irisName)
      WITH i, btId
      MATCH (l:BT_Line {btline_id: btId})
      MERGE (l)-[:SERVES_IRIS]->(i)
    } IN TRANSACTIONS OF 50000 ROWS
    """,

    # consumption
    """
    LOAD CSV WITH HEADERS FROM 'file:///consumption_daily.csv' AS row
    CALL {
      WITH row
      WITH
        trim(row.building_id) AS bid,
        CASE WHEN row.date IS NULL OR trim(row.date)='' THEN NULL ELSE date(row.date) END AS d,
        CASE WHEN row.consumption_kwh IS NULL OR trim(row.consumption_kwh)='' THEN NULL ELSE toFloat(row.consumption_kwh) END AS kwh
      WHERE bid IS NOT NULL AND bid <> '' AND d IS NOT NULL AND kwh IS NOT NULL
      MATCH (b:Building {building_id: bid})
      MERGE (day:Day {date: d})
      MERGE (b)-[r:CONSUMED_ON]->(day)
      SET r.consumption_kwh = kwh
    } IN TRANSACTIONS OF 20000 ROWS
    """,

    # labels before production
    """
    MATCH (b:Building)
    REMOVE b:Consumer
    REMOVE b:Prosumer
    """,

    """
    MATCH (b:Building)
    WHERE coalesce(b.has_pv,false) = true
    SET b:Prosumer
    """,

    """
    MATCH (b:Building)
    WHERE NOT b:Prosumer
    SET b:Consumer
    """,

    # production
    """
    LOAD CSV WITH HEADERS FROM 'file:///production_daily.csv' AS row
    CALL {
      WITH row
      WITH
        trim(row.building_id) AS bid,
        CASE WHEN row.date IS NULL OR trim(row.date)='' THEN NULL ELSE date(row.date) END AS d,
        CASE
          WHEN row.production_kwh IS NULL OR trim(row.production_kwh)='' THEN NULL
          ELSE toFloat(row.production_kwh)
        END AS kwh
      WHERE bid IS NOT NULL AND bid <> '' AND d IS NOT NULL AND kwh IS NOT NULL
      MATCH (b:Building:Prosumer {building_id: bid})
      MERGE (day:Day {date: d})
      MERGE (b)-[r:PRODUCED_ON]->(day)
      SET r.production_kwh = kwh
    } IN TRANSACTIONS OF 20000 ROWS
    """,

    # providers
    """
    LOAD CSV WITH HEADERS FROM 'file:///providers_paris.csv' AS row
    CALL {
      WITH row
      MERGE (p:Provider {provider_id: row.provider_id})
      SET
        p.name = row.provider_name,
        p.type = row.provider_type,
        p.address = row.address,
        p.location = point({latitude: toFloat(row.lat), longitude: toFloat(row.lon)}),
        p.price_eur_per_kwh = toFloat(row.price_eur_per_kwh),
        p.buy_eur_per_kwh = toFloat(row.buy_eur_per_kwh)
    } IN TRANSACTIONS OF 1000 ROWS
    """
]

# queries.py

CREATE_INDEXES = [
    # IDs / codes
    "CREATE INDEX building_id IF NOT EXISTS FOR (b:Building) ON (b.building_id)",
    "CREATE INDEX iris_code_building IF NOT EXISTS FOR (b:Building) ON (b.iris_code)",
    "CREATE INDEX iris_code_node IF NOT EXISTS FOR (i:IRIS) ON (i.code)",
    "CREATE INDEX commune_code IF NOT EXISTS FOR (c:Commune) ON (c.code)",
    "CREATE INDEX day_date IF NOT EXISTS FOR (d:Day) ON (d.date)",
    "CREATE INDEX btline_id IF NOT EXISTS FOR (l:BT_Line) ON (l.btline_id)",

    # Provider lookup
    "CREATE INDEX provider_id IF NOT EXISTS FOR (p:Provider) ON (p.provider_id)",
    "CREATE INDEX building_sell_price IF NOT EXISTS FOR (b:Building) ON (b.sell_price_eur_per_kwh)",

    # Spatial indexes (radius search)
    "CREATE POINT INDEX building_location IF NOT EXISTS FOR (b:Building) ON (b.location)",
    "CREATE POINT INDEX prosumer_location IF NOT EXISTS FOR (b:Prosumer) ON (b.location)",
    "CREATE POINT INDEX consumer_location IF NOT EXISTS FOR (b:Consumer) ON (b.location)",
    "CREATE POINT INDEX btline_centroid IF NOT EXISTS FOR (l:BT_Line) ON (l.centroid)",
    "CREATE POINT INDEX provider_location IF NOT EXISTS FOR (p:Provider) ON (p.location)",
]

LOAD_BUILDING_PRODUCER_PRICES = """
LOAD CSV WITH HEADERS FROM 'file:///building_producers_prices.csv' AS row
MATCH (b {building_id: row.building_id})
SET
  b.annual_kwh = toFloat(row.annual_kwh),
  b.mean_daily_kwh = toFloat(row.mean_daily_kwh),
  b.nonzero_day_ratio = toFloat(row.nonzero_day_ratio),
  b.variability_cv = toFloat(row.variability_cv),
  b.inferred_kwp = toFloat(row.inferred_kwp),
  b.reliability_score = toFloat(row.reliability_score),
  b.sell_price_eur_per_kwh = toFloat(row.sell_price_eur_per_kwh);
"""


LOAD_PROVIDERS = """
LOAD CSV WITH HEADERS FROM 'file:///providers_paris.csv' AS row
WITH row
MERGE (p:Provider {provider_id: row.provider_id})
SET p.name = row.provider_name,
    p.type = row.provider_type,
    p.address = row.address,
    p.location = point({latitude: toFloat(row.lat), longitude: toFloat(row.lon)}),
    p.price_eur_per_kwh = toFloat(row.price_eur_per_kwh),
    p.buy_eur_per_kwh = toFloat(row.buy_eur_per_kwh);
"""


GRID_PRODUCERS_DETAILS = """
MATCH (b)
WHERE b.building_id IN $buildingIds
  AND b.sell_price_eur_per_kwh IS NOT NULL
RETURN
  b.building_id AS building_id,
  b.annual_kwh AS annual_kwh,
  b.mean_daily_kwh AS mean_daily_kwh,
  b.nonzero_day_ratio AS nonzero_day_ratio,
  b.variability_cv AS variability_cv,
  b.inferred_kwp AS inferred_kwp,
  b.reliability_score AS reliability_score,
  b.sell_price_eur_per_kwh AS sell_price_eur_per_kwh
ORDER BY sell_price_eur_per_kwh;
"""


EVALUATE_GRID = """
WITH
  $buildingIds             AS buildingIds,
  toInteger($radiusMeters) AS radiusMeters,
  toInteger($N)            AS N,
  toFloat($T)              AS T,
  date($startDate)         AS startD,
  date($endDate)           AS endD,
  $providerId              AS providerId

MATCH (b)
WHERE b.building_id IN buildingIds
WITH collect(b) AS bs, radiusMeters, N, T, startD, endD, providerId

WITH bs, radiusMeters, N, T, startD, endD, providerId,
     size(bs) AS selectedCount

WITH bs, radiusMeters, N, T, startD, endD, providerId, selectedCount,
     any(x IN bs WHERE 'Prosumer' IN labels(x)) AS hasProsumer

// Pairwise radius check (Option 2)
// Old center-based version was:
// all(x IN bs WHERE x.location IS NOT NULL AND point.distance(x.location, cpt) <= radiusMeters) AS withinRadius
CALL {
  WITH bs, radiusMeters
  WITH bs, radiusMeters, range(0, size(bs)-1) AS idxs
  UNWIND idxs AS i
  UNWIND idxs AS j
  WITH bs, radiusMeters, i, j
  WHERE i < j
  WITH bs[i] AS bi, bs[j] AS bj, radiusMeters
  WHERE bi.location IS NULL
     OR bj.location IS NULL
     OR point.distance(bi.location, bj.location) > radiusMeters
  RETURN collect({
    a: bi.building_id,
    b: bj.building_id,
    dist_m: CASE
      WHEN bi.location IS NULL OR bj.location IS NULL THEN null
      ELSE point.distance(bi.location, bj.location)
    END
  }) AS outOfRadiusPairs
}
WITH bs, radiusMeters, N, T, startD, endD, providerId, selectedCount, hasProsumer,
     outOfRadiusPairs,
     size(outOfRadiusPairs) = 0 AS withinRadius


UNWIND bs AS x
OPTIONAL MATCH (x)-[c:CONSUMED_ON]->(d:Day)
WHERE d.date >= startD AND d.date <= endD
WITH bs, radiusMeters, N, T, startD, endD, providerId,
     selectedCount, hasProsumer, withinRadius, outOfRadiusPairs,
     x, sum(coalesce(c.consumption_kwh,0.0)) AS cons_x

OPTIONAL MATCH (x)-[p:PRODUCED_ON]->(d2:Day)
WHERE d2.date >= startD AND d2.date <= endD
WITH bs, radiusMeters, N, T, startD, endD, providerId,
     selectedCount, hasProsumer, withinRadius, outOfRadiusPairs,
     x, cons_x, sum(coalesce(p.production_kwh,0.0)) AS prod_x

WITH bs, radiusMeters, N, T, startD, endD, providerId,
     selectedCount, hasProsumer, withinRadius, outOfRadiusPairs,
     collect({id:x.building_id, cons:cons_x, prod:prod_x, isProsumer: ('Prosumer' IN labels(x))}) AS perBuilding

WITH bs, radiusMeters, N, T, startD, endD, providerId,
     selectedCount, hasProsumer, withinRadius, outOfRadiusPairs,
     reduce(t=0.0, r IN perBuilding | t + r.cons) AS totalCons,
     reduce(t=0.0, r IN perBuilding | t + r.prod) AS totalProd,
     perBuilding

WITH *, outOfRadiusPairs,
     CASE WHEN totalCons = 0.0 THEN 0.0 ELSE (totalProd / totalCons) END AS coverageRatio,
     (totalProd - totalCons) AS surplusKwh,
     (totalCons - totalProd) AS deficitKwh

WITH *, outOfRadiusPairs,
  CASE
    WHEN deficitKwh > 0 AND coverageRatio < T THEN false
    ELSE true
  END AS coverageOk

CALL {
  WITH providerId
  MATCH (p:Provider)
  WHERE providerId IS NULL OR p.provider_id = providerId
  RETURN p
  ORDER BY p.price_eur_per_kwh ASC
  LIMIT 1
}
WITH *, outOfRadiusPairs,
     p.provider_id AS chosenProviderId,
     p.price_eur_per_kwh AS price

WITH *, outOfRadiusPairs,
     reduce(cost=0.0, r IN perBuilding |
       cost + (CASE WHEN (r.cons - r.prod) > 0 THEN (r.cons - r.prod) * price ELSE 0.0 END)
     ) AS sumIndividualCost,
     (CASE WHEN deficitKwh > 0 THEN deficitKwh * price ELSE 0.0 END) AS gridCost

WITH *, outOfRadiusPairs,
     (sumIndividualCost - gridCost) AS monetaryGain,
     (gridCost < sumIndividualCost) AS buyingAsEntityIsBetter

WITH *, outOfRadiusPairs,
  CASE
    // Case 1: surplus or self-sufficient grid → automatically valid
    WHEN surplusKwh >= 0 THEN
      (selectedCount >= N AND hasProsumer AND withinRadius)

    // Case 2: deficit grid → economic check required
    ELSE
      (selectedCount >= N
       AND hasProsumer
       AND withinRadius
       AND coverageOk
       AND buyingAsEntityIsBetter)
  END AS isValid

RETURN
  isValid,
  selectedCount, N,
  hasProsumer,
  withinRadius,
  coverageOk,
  buyingAsEntityIsBetter,
  totalCons,
  totalProd,
  coverageRatio,
  surplusKwh,
  deficitKwh,
  chosenProviderId,
  price,
  sumIndividualCost,
  gridCost,
  monetaryGain,
  outOfRadiusPairs,
  perBuilding;
"""


EVALUATE_GRID_PAIRWISE = """
WITH
  $buildingIds             AS buildingIds,
  toInteger($radiusMeters) AS radiusMeters,
  toInteger($N)            AS N,
  toFloat($T)              AS T,
  date($startDate)         AS startD,
  date($endDate)           AS endD,
  $providerId              AS providerId,
  $buyerProviderId         AS buyerProviderId

MATCH (b)
WHERE b.building_id IN buildingIds
WITH collect(b) AS bs, radiusMeters, N, T, startD, endD, providerId, buyerProviderId
WITH bs, radiusMeters, N, T, startD, endD, providerId, buyerProviderId,
     size(bs) AS selectedCount,
     any(x IN bs WHERE 'Prosumer' IN labels(x)) AS hasProsumer

// ------------------------
// Pairwise distance checks
// ------------------------
UNWIND range(0, size(bs)-1) AS i
UNWIND range(i+1, size(bs)-1) AS j
WITH bs, radiusMeters, N, T, startD, endD, providerId, buyerProviderId, selectedCount, hasProsumer, i, j,
     bs[i] AS bi, bs[j] AS bj
WITH bs, radiusMeters, N, T, startD, endD, providerId, buyerProviderId, selectedCount, hasProsumer,
     collect(
       CASE
         WHEN bi.location IS NULL OR bj.location IS NULL THEN
           {b1: bi.building_id, b2: bj.building_id, dist_m: null, reason: "missing_location"}
         WHEN point.distance(bi.location, bj.location) > radiusMeters THEN
           {b1: bi.building_id, b2: bj.building_id, dist_m: point.distance(bi.location, bj.location), reason: "too_far"}
         ELSE null
       END
     ) AS pairChecks

WITH bs, radiusMeters, N, T, startD, endD, providerId, buyerProviderId, selectedCount, hasProsumer,
     [x IN pairChecks WHERE x IS NOT NULL] AS outOfRadiusPairs,
     (size([x IN pairChecks WHERE x IS NOT NULL]) = 0) AS withinRadius

// ------------------------
// Per-building energy
// ------------------------
UNWIND bs AS x
OPTIONAL MATCH (x)-[c:CONSUMED_ON]->(d:Day)
WHERE d.date >= startD AND d.date <= endD
WITH bs, radiusMeters, N, T, startD, endD, providerId, buyerProviderId, selectedCount, hasProsumer, withinRadius, outOfRadiusPairs,
     x, sum(coalesce(c.consumption_kwh,0.0)) AS cons_x
OPTIONAL MATCH (x)-[p:PRODUCED_ON]->(d2:Day)
WHERE d2.date >= startD AND d2.date <= endD
WITH bs, radiusMeters, N, T, startD, endD, providerId, buyerProviderId, selectedCount, hasProsumer, withinRadius, outOfRadiusPairs,
     x, cons_x, sum(coalesce(p.production_kwh,0.0)) AS prod_x

WITH bs, radiusMeters, N, T, startD, endD, providerId, buyerProviderId, selectedCount, hasProsumer, withinRadius, outOfRadiusPairs,
     collect({id:x.building_id, cons:cons_x, prod:prod_x, isProsumer: ('Prosumer' IN labels(x))}) AS perBuilding

WITH *,
     reduce(t=0.0, r IN perBuilding | t + r.cons) AS totalCons,
     reduce(t=0.0, r IN perBuilding | t + r.prod) AS totalProd

WITH *,
     CASE WHEN totalCons = 0.0 THEN 0.0 ELSE (totalProd / totalCons) END AS coverageRatio,
     (totalProd - totalCons) AS surplusKwh,
     (totalCons - totalProd) AS deficitKwh

WITH *,
  CASE
    WHEN deficitKwh > 0 AND coverageRatio < T THEN false
    ELSE true
  END AS coverageOk

// ------------------------
// Choose provider to BUY deficit
// ------------------------
CALL {
  WITH providerId
  MATCH (p:Provider)
  WHERE providerId IS NULL OR p.provider_id = providerId
  RETURN p
  ORDER BY p.price_eur_per_kwh ASC
  LIMIT 1
}
WITH *,
     p.provider_id AS chosenProviderId,
     p.price_eur_per_kwh AS buyPrice

// ------------------------
// Choose provider to SELL surplus
// ------------------------
CALL {
  WITH buyerProviderId
  MATCH (bp:Provider)
  WHERE buyerProviderId IS NULL OR bp.provider_id = buyerProviderId
  RETURN bp
  ORDER BY bp.buy_eur_per_kwh DESC
  LIMIT 1
}
WITH *,
     bp.provider_id AS chosenBuyerProviderId,
     bp.buy_eur_per_kwh AS sellPrice

// ------------------------
// Cost & gains
// ------------------------
// individual cost: only consumers with deficit pay (cons-prod>0)
WITH *,
     reduce(cost=0.0, r IN perBuilding |
       cost + (CASE WHEN (r.cons - r.prod) > 0 THEN (r.cons - r.prod) * buyPrice ELSE 0.0 END)
     ) AS sumIndividualCost,

     // grid buys ONLY global deficit (if any)
     (CASE WHEN deficitKwh > 0 THEN deficitKwh * buyPrice ELSE 0.0 END) AS gridBuyCost,

     // grid sells ONLY global surplus (if any)
     (CASE WHEN surplusKwh > 0 THEN surplusKwh * sellPrice ELSE 0.0 END) AS gridSellRevenue

WITH *,
     // baseline gain: entity-buying advantage (>=0 if grid buy cheaper vs individuals)
     (sumIndividualCost - gridBuyCost) AS gainFromPooling,

     // additional gain: selling surplus
     gridSellRevenue AS gainFromSelling,

     // total gain
     ((sumIndividualCost - gridBuyCost) + gridSellRevenue) AS monetaryGain,

     (gridBuyCost < sumIndividualCost) AS buyingAsEntityIsBetter

WITH *,
  CASE
    WHEN surplusKwh >= 0 THEN
      (selectedCount >= N AND hasProsumer AND withinRadius)
    ELSE
      (selectedCount >= N
       AND hasProsumer
       AND withinRadius
       AND coverageOk)
  END AS isValid


RETURN
  isValid,
  selectedCount, N,
  hasProsumer,
  withinRadius,
  outOfRadiusPairs,
  coverageOk,
  buyingAsEntityIsBetter,
  totalCons,
  totalProd,
  coverageRatio,
  surplusKwh,
  deficitKwh,
  chosenProviderId,
  buyPrice,
  chosenBuyerProviderId,
  sellPrice,
  sumIndividualCost,
  gridBuyCost,
  gridSellRevenue,
  gainFromPooling,
  gainFromSelling,
  monetaryGain,
  perBuilding;
"""

