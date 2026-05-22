import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import maplibregl, { type Map as MapLibreMap, type StyleSpecification } from "maplibre-gl";

import "maplibre-gl/dist/maplibre-gl.css";

export type GeoVisualMode = "globe" | "country_satellite" | "city_satellite" | "property_satellite";
export type SatelliteTileStatus = "tiles-loading" | "tiles-ready" | "tiles-error";

export type SatelliteCamera = {
  center: [number, number];
  zoom: number;
  pitch: number;
  bearing: number;
};

export type SatelliteBounds = {
  minLng: number;
  minLat: number;
  maxLng: number;
  maxLat: number;
};

export type SatelliteMarker = {
  id: string;
  kind: "city" | "city_cluster" | "property" | "property_cluster";
  lat: number;
  lng: number;
  label: string;
  country?: string;
  city?: string;
  count?: number;
  selected?: boolean;
  meta?: string;
  cityIds?: string[];
  cityNames?: string[];
  cityCount?: number;
  candidateCount?: number;
  sourceCount?: number;
  reviewCount?: number;
  scenes?: Record<string, number>;
  bounds?: SatelliteBounds;
  childMarkers?: ProjectedSatelliteMarker[];
};

type ProjectedSatelliteMarker = SatelliteMarker & {
  x: number;
  y: number;
  visible: boolean;
  spiderChild?: boolean;
};

type SatelliteNavigatorProps = {
  mode: GeoVisualMode;
  camera: SatelliteCamera;
  markers: SatelliteMarker[];
  stageSize: { width: number; height: number };
  mock: boolean;
  tileTemplate: string;
  tileSize: number;
  attribution: string;
  onMarkerSelect: (marker: SatelliteMarker) => void;
};

const SATELLITE_CITY_CLUSTER_RADIUS_PX = 104;
const SATELLITE_PROPERTY_CLUSTER_RADIUS_PX = 82;
const SPIDER_BASE_RADIUS_PX = 78;
const SPIDER_RING_GAP_PX = 44;
const SPIDER_FIRST_RING_CAPACITY = 8;
const SPIDER_EDGE_PADDING_PX = 34;

export default function SatelliteNavigator({
  mode,
  camera,
  markers,
  stageSize,
  mock,
  tileTemplate,
  tileSize,
  attribution,
  onMarkerSelect,
}: SatelliteNavigatorProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const markersRef = useRef(markers);
  const [tileStatus, setTileStatus] = useState<SatelliteTileStatus>("tiles-loading");
  const [projectedMarkers, setProjectedMarkers] = useState<ProjectedSatelliteMarker[]>([]);
  const [expandedSatelliteClusterKey, setExpandedSatelliteClusterKey] = useState("");
  const [spiderSatelliteClusterKey, setSpiderSatelliteClusterKey] = useState("");
  const markerSignature = useMemo(
    () => markers.map((marker) => `${marker.kind}:${marker.id}`).join("|"),
    [markers],
  );

  useEffect(() => {
    markersRef.current = markers;
  }, [markers]);

  useEffect(() => {
    setExpandedSatelliteClusterKey("");
    setSpiderSatelliteClusterKey("");
  }, [markerSignature, mode]);

  const refreshProjectedMarkers = useCallback(() => {
    const map = mapRef.current;
    const container = containerRef.current;
    if (!map || !container) {
      setProjectedMarkers([]);
      return;
    }
    const rect = container.getBoundingClientRect();
    setProjectedMarkers(
      markersRef.current.map((marker) => {
        const point = map.project([marker.lng, marker.lat]);
        return {
          ...marker,
          x: point.x,
          y: point.y,
          visible:
            point.x >= -48
            && point.x <= rect.width + 48
            && point.y >= -48
            && point.y <= rect.height + 48,
        };
      }),
    );
  }, []);

  useEffect(() => {
    if (mock) {
      setProjectedMarkers(projectMockSatelliteMarkers(markers, camera, stageSize));
      return;
    }
    setProjectedMarkers([]);
  }, [camera, markers, mock, stageSize]);

  useEffect(() => {
    if (!mock) {
      maplibregl.prewarm();
    }
  }, [mock]);

  useEffect(() => {
    if (mock || !containerRef.current || mapRef.current) {
      return;
    }
    setTileStatus("tiles-loading");
    const style: StyleSpecification = {
      version: 8,
      sources: {
        satellite: {
          type: "raster",
          tiles: [tileTemplate],
          tileSize,
          attribution,
        },
      },
      layers: [
        {
          id: "satellite",
          type: "raster",
          source: "satellite",
          paint: {
            "raster-brightness-min": 0.08,
            "raster-brightness-max": 0.86,
            "raster-contrast": 0.18,
            "raster-saturation": -0.16,
          },
        },
      ],
    };
    const map = new maplibregl.Map({
      container: containerRef.current,
      style,
      center: camera.center,
      zoom: camera.zoom,
      pitch: camera.pitch,
      bearing: camera.bearing,
      attributionControl: { compact: true },
      maxPitch: 68,
    });
    mapRef.current = map;
    const update = () => refreshProjectedMarkers();
    const markReady = () => setTileStatus((current) => current === "tiles-error" ? current : "tiles-ready");
    map.on("load", update);
    map.on("move", update);
    map.on("resize", update);
    map.on("idle", markReady);
    map.on("error", () => setTileStatus("tiles-error"));
    return () => {
      map.off("load", update);
      map.off("move", update);
      map.off("resize", update);
      map.off("idle", markReady);
      map.remove();
      mapRef.current = null;
    };
  }, [attribution, camera.bearing, camera.center, camera.pitch, camera.zoom, mock, refreshProjectedMarkers, tileSize, tileTemplate]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || mock) {
      return;
    }
    setTileStatus("tiles-loading");
    map.easeTo({
      center: camera.center,
      zoom: camera.zoom,
      pitch: camera.pitch,
      bearing: camera.bearing,
      duration: 850,
      essential: true,
    });
    const timer = window.setTimeout(refreshProjectedMarkers, 900);
    return () => window.clearTimeout(timer);
  }, [camera, mock, refreshProjectedMarkers]);

  useEffect(() => {
    if (mock) {
      return;
    }
    prefetchSatelliteTiles(tileTemplate, camera, mode);
  }, [camera, mock, mode, tileTemplate]);

  useEffect(() => {
    if (mock || !mapRef.current) {
      return;
    }
    refreshProjectedMarkers();
  }, [markers, mock, refreshProjectedMarkers]);

  const rawVisibleMarkers = mock ? projectMockSatelliteMarkers(markers, camera, stageSize) : projectedMarkers;
  const visibleMarkers = useMemo(
    () => clusterProjectedSatelliteMarkers(rawVisibleMarkers, {
      expandedClusterKey: expandedSatelliteClusterKey,
      mode,
      spiderClusterKey: spiderSatelliteClusterKey,
      stageSize,
    }),
    [expandedSatelliteClusterKey, mode, rawVisibleMarkers, spiderSatelliteClusterKey, stageSize],
  );
  const handleMarkerSelect = useCallback((marker: ProjectedSatelliteMarker) => {
    if (!isSatelliteClusterMarker(marker)) {
      onMarkerSelect(marker);
      return;
    }
    if (expandedSatelliteClusterKey === marker.id) {
      setSpiderSatelliteClusterKey(marker.id);
      return;
    }
    setExpandedSatelliteClusterKey(marker.id);
    setSpiderSatelliteClusterKey("");
    const bounds = marker.bounds;
    if (!bounds || mock) {
      return;
    }
    const map = mapRef.current;
    if (!map) {
      return;
    }
    const isPropertyCluster = marker.kind === "property_cluster";
    const nextCamera = cameraFromBounds(bounds, {
      pitch: camera.pitch,
      bearing: camera.bearing,
      minZoom: isPropertyCluster
        ? Math.min(15.2, Math.max(camera.zoom + 0.9, 11.8))
        : Math.min(8.4, Math.max(camera.zoom + 0.9, 5.8)),
      maxZoom: isPropertyCluster ? 17.2 : 9.8,
    });
    map.easeTo({
      center: nextCamera.center,
      zoom: nextCamera.zoom,
      pitch: nextCamera.pitch,
      bearing: nextCamera.bearing,
      duration: 900,
      essential: true,
    });
    window.setTimeout(refreshProjectedMarkers, 950);
  }, [
    camera.bearing,
    camera.pitch,
    camera.zoom,
    expandedSatelliteClusterKey,
    mock,
    onMarkerSelect,
    refreshProjectedMarkers,
  ]);
  const modeLabel = satelliteModeLabel(mode);

  return (
    <div
      className={`geo-visual-layer satellite-navigator ${mock ? "mock-satellite" : ""}`}
      aria-label="Satellite opportunity map"
      data-satellite-mode={mode}
      data-tile-status={mock ? "tiles-ready" : tileStatus}
      data-expanded-satellite-cluster={expandedSatelliteClusterKey}
      data-spider-satellite-cluster={spiderSatelliteClusterKey}
    >
      <div ref={containerRef} className="satellite-map-surface" aria-hidden={mock ? "true" : "false"} />
      {mock && <canvas className="satellite-mock-canvas" width={stageSize.width} height={stageSize.height} />}
      <div className="satellite-shade" aria-hidden="true" />
      {(tileStatus !== "tiles-ready" || mock) && (
        <div className="satellite-status">
          <span>{modeLabel}</span>
          {!mock && tileStatus === "tiles-loading" && <strong>Loading imagery</strong>}
          {!mock && tileStatus === "tiles-error" && <strong>Imagery fallback active</strong>}
        </div>
      )}
      <div className="satellite-marker-layer" aria-label="Satellite marker layer">
        {visibleMarkers.map((marker) => (
          <SatelliteMarkerButton
            key={`${marker.kind}-${marker.id}`}
            marker={marker}
            onSelect={handleMarkerSelect}
          />
        ))}
      </div>
    </div>
  );
}

function SatelliteMarkerButton({
  marker,
  onSelect,
}: {
  marker: ProjectedSatelliteMarker;
  onSelect: (marker: ProjectedSatelliteMarker) => void;
}) {
  const showCount = satelliteMarkerShowsCount(marker);
  return (
    <button
      className={`satellite-marker satellite-marker-${marker.kind.replace("_", "-")} ${marker.selected ? "active" : ""} ${marker.spiderChild ? "spider-child" : ""}`}
      type="button"
      aria-label={satelliteMarkerAriaLabel(marker)}
      title={satelliteMarkerTitle(marker)}
      data-satellite-marker-kind={marker.kind}
      onClick={() => onSelect(marker)}
      style={{
        opacity: marker.visible ? 1 : 0,
        pointerEvents: marker.visible ? "auto" : "none",
        transform: `translate3d(${marker.x}px, ${marker.y}px, 0) translate(-50%, -50%)`,
      }}
    >
      <span className="satellite-marker-pulse" aria-hidden="true" />
      <span className="satellite-marker-core">
        {showCount
          ? formatNumber(marker.count ?? marker.candidateCount ?? marker.cityCount ?? 0)
          : <span className="satellite-marker-pin" aria-hidden="true" />}
      </span>
      <span className="satellite-marker-label">
        {marker.label}
        {marker.meta && <small>{marker.meta}</small>}
      </span>
    </button>
  );
}

function projectMockSatelliteMarkers(
  markers: SatelliteMarker[],
  camera: SatelliteCamera,
  stageSize: { width: number; height: number },
): ProjectedSatelliteMarker[] {
  const width = Math.max(320, stageSize.width);
  const height = Math.max(320, stageSize.height);
  const scale = Math.pow(2, camera.zoom - 4);
  return markers.map((marker, index) => {
    const ring = index % 7;
    const lngDelta = normalizeLongitudeDelta(marker.lng - camera.center[0]);
    const latDelta = marker.lat - camera.center[1];
    const x = clamp(width / 2 + lngDelta * 18 * scale + (ring - 3) * 10, 54, width - 54);
    const y = clamp(height / 2 - latDelta * 22 * scale + ((index % 5) - 2) * 8, 78, height - 58);
    return {
      ...marker,
      x,
      y,
      visible: true,
    };
  });
}

function clusterProjectedSatelliteMarkers(
  projected: ProjectedSatelliteMarker[],
  {
    expandedClusterKey,
    mode,
    spiderClusterKey,
    stageSize,
  }: {
    expandedClusterKey: string;
    mode: GeoVisualMode;
    spiderClusterKey: string;
    stageSize: { width: number; height: number };
  },
): ProjectedSatelliteMarker[] {
  const clusterTarget = satelliteClusterTargetForMode(mode);
  if (!clusterTarget) {
    return projected;
  }

  const passthrough: ProjectedSatelliteMarker[] = [];
  const clusterableMarkers: ProjectedSatelliteMarker[] = [];
  projected.forEach((marker) => {
    if (marker.kind !== clusterTarget.markerKind || !marker.visible || marker.selected) {
      passthrough.push(marker);
      return;
    }
    clusterableMarkers.push(marker);
  });

  const clustered: ProjectedSatelliteMarker[] = [];
  buildSatelliteClusters(clusterableMarkers, clusterTarget.radiusPx).forEach((cluster) => {
    if (cluster.length === 1) {
      clustered.push(cluster[0]);
      return;
    }
    const clusterMarker = clusterTarget.clusterKind === "city_cluster"
      ? makeSatelliteCityClusterMarker(cluster, expandedClusterKey)
      : makeSatellitePropertyClusterMarker(cluster, expandedClusterKey);
    if (clusterMarker.id === spiderClusterKey) {
      clustered.push(...spiderSatelliteCluster(cluster, clusterMarker, stageSize));
      return;
    }
    clustered.push(clusterMarker);
  });

  return [...passthrough, ...clustered];
}

function satelliteClusterTargetForMode(mode: GeoVisualMode): {
  markerKind: SatelliteMarker["kind"];
  clusterKind: "city_cluster" | "property_cluster";
  radiusPx: number;
} | null {
  if (mode === "country_satellite") {
    return {
      markerKind: "city",
      clusterKind: "city_cluster",
      radiusPx: SATELLITE_CITY_CLUSTER_RADIUS_PX,
    };
  }
  if (mode === "city_satellite") {
    return {
      markerKind: "property",
      clusterKind: "property_cluster",
      radiusPx: SATELLITE_PROPERTY_CLUSTER_RADIUS_PX,
    };
  }
  return null;
}

function buildSatelliteClusters(
  markers: ProjectedSatelliteMarker[],
  radiusPx: number,
): ProjectedSatelliteMarker[][] {
  const clusters: ProjectedSatelliteMarker[][] = [];
  markers.forEach((marker) => {
    let closestClusterIndex = -1;
    let closestDistance = Number.POSITIVE_INFINITY;
    clusters.forEach((cluster, index) => {
      const center = satelliteProjectedClusterCenter(cluster);
      const distance = screenDistance(marker, center);
      if (distance <= radiusPx && distance < closestDistance) {
        closestClusterIndex = index;
        closestDistance = distance;
      }
    });

    if (closestClusterIndex >= 0) {
      clusters[closestClusterIndex].push(marker);
      return;
    }
    clusters.push([marker]);
  });
  return clusters;
}

function makeSatellitePropertyClusterMarker(
  cluster: ProjectedSatelliteMarker[],
  expandedClusterKey: string,
): ProjectedSatelliteMarker {
  const center = satelliteProjectedClusterCenter(cluster);
  const propertyIds = cluster.map((marker) => marker.id).sort();
  const scenes: Record<string, number> = {};
  cluster.forEach((marker) => {
    Object.entries(marker.scenes || {}).forEach(([scene, count]) => {
      scenes[scene] = (scenes[scene] || 0) + count;
    });
  });
  const candidateCount = cluster.reduce((total, marker) => total + (marker.candidateCount ?? marker.count ?? 1), 0);
  const sourceCount = cluster.reduce((total, marker) => total + (marker.sourceCount || 0), 0);
  const reviewCount = cluster.reduce((total, marker) => total + (marker.reviewCount || 0), 0);
  const bounds = satelliteClusterBounds(cluster);
  const id = `satellite_property_cluster::${propertyIds.join("|")}`;
  const topScene = Object.entries(scenes).sort((a, b) => b[1] - a[1])[0]?.[0];
  return {
    id,
    kind: "property_cluster",
    lat: average(cluster.map((marker) => marker.lat)),
    lng: average(cluster.map((marker) => marker.lng)),
    label: `${formatNumber(candidateCount)} sites`,
    country: cluster[0]?.country || "",
    city: cluster[0]?.city || "",
    count: candidateCount,
    selected: id === expandedClusterKey,
    meta: topScene ? sceneLabel(topScene) : "Property cluster",
    candidateCount,
    sourceCount,
    reviewCount,
    scenes,
    bounds,
    childMarkers: cluster,
    x: center.x,
    y: center.y,
    visible: true,
  };
}

function makeSatelliteCityClusterMarker(
  cluster: ProjectedSatelliteMarker[],
  expandedClusterKey: string,
): ProjectedSatelliteMarker {
  const center = satelliteProjectedClusterCenter(cluster);
  const cityIds = cluster.map((marker) => marker.id).sort();
  const scenes: Record<string, number> = {};
  cluster.forEach((marker) => {
    Object.entries(marker.scenes || {}).forEach(([scene, count]) => {
      scenes[scene] = (scenes[scene] || 0) + count;
    });
  });
  const candidateCount = cluster.reduce((total, marker) => total + (marker.candidateCount ?? marker.count ?? 0), 0);
  const sourceCount = cluster.reduce((total, marker) => total + (marker.sourceCount || 0), 0);
  const reviewCount = cluster.reduce((total, marker) => total + (marker.reviewCount || 0), 0);
  const bounds = satelliteClusterBounds(cluster);
  const id = `satellite_city_cluster::${cityIds.join("|")}`;
  return {
    id,
    kind: "city_cluster",
    lat: average(cluster.map((marker) => marker.lat)),
    lng: average(cluster.map((marker) => marker.lng)),
    label: `${formatNumber(cluster.length)} cities`,
    country: cluster[0]?.country || "",
    count: cluster.length,
    selected: id === expandedClusterKey,
    meta: `${formatNumber(candidateCount)} candidates`,
    cityIds,
    cityNames: cluster.map((marker) => marker.city || marker.label).sort(),
    cityCount: cluster.length,
    candidateCount,
    sourceCount,
    reviewCount,
    scenes,
    bounds,
    childMarkers: cluster,
    x: center.x,
    y: center.y,
    visible: true,
  };
}

function spiderSatelliteCluster(
  cluster: ProjectedSatelliteMarker[],
  clusterMarker: ProjectedSatelliteMarker,
  stageSize: { width: number; height: number },
): ProjectedSatelliteMarker[] {
  return cluster.map((marker, index) => {
    const offset = spiderOffset(index, cluster.length);
    return {
      ...marker,
      x: roundScreenCoordinate(clampScreenX(clusterMarker.x + offset.x, stageSize.width)),
      y: roundScreenCoordinate(clampScreenY(clusterMarker.y + offset.y, stageSize.height)),
      visible: true,
      spiderChild: true,
    };
  });
}

function satelliteClusterBounds(cluster: ProjectedSatelliteMarker[]): SatelliteBounds | undefined {
  return boundsFromCoordinates(cluster.map((marker) => ({ lat: marker.lat, lng: marker.lng }))) || undefined;
}

function satelliteProjectedClusterCenter(cluster: ProjectedSatelliteMarker[]): { x: number; y: number } {
  return {
    x: roundScreenCoordinate(average(cluster.map((marker) => marker.x))),
    y: roundScreenCoordinate(average(cluster.map((marker) => marker.y))),
  };
}

function prefetchSatelliteTiles(
  tileTemplate: string,
  camera: SatelliteCamera,
  mode: GeoVisualMode,
) {
  const zoom = mode === "property_satellite"
    ? clamp(Math.round(camera.zoom), 15, 18)
    : clamp(Math.round(camera.zoom), 4, 6);
  const center = satelliteTileCoordinate(camera.center[0], camera.center[1], zoom);
  const radius = mode === "property_satellite" ? 1 : 0;
  for (let dx = -radius; dx <= radius; dx += 1) {
    for (let dy = -radius; dy <= radius; dy += 1) {
      const x = center.x + dx;
      const y = center.y + dy;
      const maxIndex = (2 ** zoom) - 1;
      if (x < 0 || y < 0 || x > maxIndex || y > maxIndex) {
        continue;
      }
      const url = formatSatelliteTileUrl(tileTemplate, zoom, y, x);
      window.setTimeout(() => {
        void fetch(url, { cache: "force-cache" }).catch(() => undefined);
      }, 0);
    }
  }
}

function satelliteMarkerAriaLabel(marker: SatelliteMarker): string {
  if (marker.kind === "city_cluster") {
    return `${formatNumber(marker.cityCount || marker.count || 0)} clustered cities in ${marker.country || "country"}, ${formatNumber(marker.candidateCount || 0)} candidate properties, click to zoom or expand`;
  }
  if (marker.kind === "property_cluster") {
    const location = [marker.city, marker.country].filter(Boolean).join(", ") || "city";
    return `${formatNumber(marker.candidateCount || marker.count || 0)} clustered property markers in ${location}, click to zoom or expand`;
  }
  if (marker.kind === "city") {
    return `${marker.city || marker.label}, ${marker.country || "country"}, ${formatNumber(marker.candidateCount ?? marker.count ?? 0)} candidate properties, city satellite marker`;
  }
  return `${marker.label}, property satellite marker`;
}

function satelliteMarkerTitle(marker: SatelliteMarker): string {
  if (marker.kind === "city_cluster") {
    const topCities = (marker.cityNames || []).slice(0, 4).join(" · ");
    const topScenes = Object.entries(marker.scenes || {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 2)
      .map(([scene, count]) => `${sceneLabel(scene)} ${count}`)
      .join(" · ");
    return [
      `${formatNumber(marker.cityCount || marker.count || 0)} cities in ${marker.country || "country"}`,
      `${formatNumber(marker.candidateCount || 0)} candidates`,
      `${formatNumber(marker.reviewCount || 0)} review`,
      `${formatNumber(marker.sourceCount || 0)} sources`,
      topCities,
      topScenes,
      "city cluster satellite marker",
      "Click to zoom; click again to expand city markers",
    ].filter(Boolean).join("\n");
  }
  if (marker.kind === "property_cluster") {
    const topScenes = Object.entries(marker.scenes || {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([scene, count]) => `${sceneLabel(scene)} ${count}`)
      .join(" · ");
    return [
      `${formatNumber(marker.candidateCount || marker.count || 0)} properties`,
      [marker.city, marker.country].filter(Boolean).join(", "),
      `${formatNumber(marker.reviewCount || 0)} review`,
      `${formatNumber(marker.sourceCount || 0)} sources`,
      topScenes,
      "property cluster satellite marker",
      "Click to zoom; click again to expand property markers",
    ].filter(Boolean).join("\n");
  }
  if (marker.kind === "city") {
    const topScenes = Object.entries(marker.scenes || {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 2)
      .map(([scene, count]) => `${sceneLabel(scene)} ${count}`)
      .join(" · ");
    return [
      `${marker.city || marker.label}, ${marker.country || ""}`.trim(),
      `${formatNumber(marker.candidateCount ?? marker.count ?? 0)} candidates`,
      `${formatNumber(marker.reviewCount || 0)} review`,
      `${formatNumber(marker.sourceCount || 0)} sources`,
      topScenes,
      "city satellite marker",
    ].filter(Boolean).join("\n");
  }
  return satelliteMarkerAriaLabel(marker);
}

function isSatelliteClusterMarker(marker: SatelliteMarker): boolean {
  return marker.kind === "city_cluster" || marker.kind === "property_cluster";
}

function satelliteMarkerShowsCount(marker: SatelliteMarker): boolean {
  if (marker.kind === "property") {
    return (marker.count ?? marker.candidateCount ?? 1) > 1;
  }
  return true;
}

function cameraFromBounds(
  bounds: SatelliteBounds,
  config: { pitch: number; bearing: number; minZoom: number; maxZoom: number },
): SatelliteCamera {
  const lngSpan = Math.max(0.01, Math.abs(bounds.maxLng - bounds.minLng));
  const latSpan = Math.max(0.01, Math.abs(bounds.maxLat - bounds.minLat));
  const span = Math.max(lngSpan, latSpan);
  const zoom = clamp(8.2 - Math.log2(span), config.minZoom, config.maxZoom);
  return {
    center: [(bounds.minLng + bounds.maxLng) / 2, (bounds.minLat + bounds.maxLat) / 2],
    zoom,
    pitch: config.pitch,
    bearing: config.bearing,
  };
}

function boundsFromCoordinates(points: Array<{ lat: number; lng: number }>): SatelliteBounds | null {
  const usable = points.filter((point) => Number.isFinite(point.lat) && Number.isFinite(point.lng));
  if (usable.length === 0) {
    return null;
  }
  return usable.reduce<SatelliteBounds>(
    (bounds, point) => ({
      minLng: Math.min(bounds.minLng, point.lng),
      minLat: Math.min(bounds.minLat, point.lat),
      maxLng: Math.max(bounds.maxLng, point.lng),
      maxLat: Math.max(bounds.maxLat, point.lat),
    }),
    {
      minLng: usable[0].lng,
      minLat: usable[0].lat,
      maxLng: usable[0].lng,
      maxLat: usable[0].lat,
    },
  );
}

function spiderOffset(index: number, total: number): { x: number; y: number } {
  let ring = 1;
  let ringStart = 0;
  let capacity = SPIDER_FIRST_RING_CAPACITY;
  while (index >= ringStart + capacity) {
    ringStart += capacity;
    ring += 1;
    capacity = SPIDER_FIRST_RING_CAPACITY + (ring - 1) * 6;
  }

  const itemsInRing = Math.min(capacity, total - ringStart);
  const slot = index - ringStart;
  const radius = SPIDER_BASE_RADIUS_PX + (ring - 1) * SPIDER_RING_GAP_PX;
  const angle = -Math.PI / 2 + slot * (Math.PI * 2 / itemsInRing) + (ring % 2 === 0 ? Math.PI / itemsInRing : 0);
  return {
    x: Math.cos(angle) * radius,
    y: Math.sin(angle) * radius,
  };
}

function screenDistance(
  marker: { x: number; y: number },
  center: { x: number; y: number },
): number {
  return Math.hypot(marker.x - center.x, marker.y - center.y);
}

function average(values: number[]): number {
  if (values.length === 0) {
    return 0;
  }
  return values.reduce((total, value) => total + value, 0) / values.length;
}

function clampScreenX(value: number, width: number): number {
  return clamp(value, SPIDER_EDGE_PADDING_PX, Math.max(SPIDER_EDGE_PADDING_PX, width - SPIDER_EDGE_PADDING_PX));
}

function clampScreenY(value: number, height: number): number {
  return clamp(value, SPIDER_EDGE_PADDING_PX, Math.max(SPIDER_EDGE_PADDING_PX, height - SPIDER_EDGE_PADDING_PX));
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function roundScreenCoordinate(value: number): number {
  return Math.round(value * 10) / 10;
}

function normalizeLongitudeDelta(delta: number): number {
  if (delta > 180) {
    return delta - 360;
  }
  if (delta < -180) {
    return delta + 360;
  }
  return delta;
}

function formatSatelliteTileUrl(template: string, z: number, y: number, x: number): string {
  return template
    .replaceAll("{z}", String(z))
    .replaceAll("{y}", String(y))
    .replaceAll("{x}", String(x));
}

function satelliteTileCoordinate(lng: number, lat: number, zoom: number): { x: number; y: number } {
  const latRad = clamp(lat, -85.05112878, 85.05112878) * Math.PI / 180;
  const scale = 2 ** zoom;
  const x = Math.floor(((lng + 180) / 360) * scale);
  const y = Math.floor(
    ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * scale,
  );
  return {
    x: clamp(x, 0, scale - 1),
    y: clamp(y, 0, scale - 1),
  };
}

function satelliteModeLabel(mode: GeoVisualMode): string {
  if (mode === "property_satellite") {
    return "Street-level satellite";
  }
  if (mode === "city_satellite") {
    return "City satellite";
  }
  return "Country satellite";
}

function sceneLabel(scene: string): string {
  return scene
    .split("_")
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "0";
  }
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
}
