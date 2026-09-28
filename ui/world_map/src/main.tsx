import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import Globe, { type GlobeMethods } from "react-globe.gl";
import { feature as topoFeature } from "topojson-client";
import countries110m from "world-atlas/countries-110m.json";
import {
  ArrowLeft,
  Bell,
  Bot,
  ChevronDown,
  ClipboardPlus,
  ExternalLink,
  FileSpreadsheet,
  ImageOff,
  LayoutGrid,
  Languages,
  List,
  LogIn,
  LogOut,
  MapPin,
  RefreshCw,
  Search,
  UserRound,
  X,
} from "lucide-react";
import type { Feature, FeatureCollection, Geometry } from "geojson";
import MapInteractionChrome, {
  type MapHoverMarker,
  type MapPointerPosition,
} from "./MapInteractionChrome";
import type { GeoVisualMode, SatelliteBounds, SatelliteCamera, SatelliteMarker } from "./SatelliteNavigator";
import {
  ServiceRequestDrawer,
  UpdatesDrawer,
  type ProductUpdate,
} from "./ServiceRequestPanels";

import "./styles.css";

const SatelliteNavigator = React.lazy(() => import("./SatelliteNavigator"));

type HeroImage = {
  url: string;
  alt_text: string;
  source_name: string;
  source_url: string;
  source_date?: string | null;
  license?: string | null;
};

type Entity = {
  property_id: string;
  country: string;
  city: string;
  city_assignment?: {
    city_id?: string | null;
    canonical_city?: string;
    source_city: string;
    locality?: string | null;
    admin_area_1?: string | null;
    admin_area_2?: string | null;
    mapping_status: "verified" | "review_required" | "unmapped";
    mapping_method: string;
    source_authority?: string | null;
    source_url?: string | null;
  } | null;
  property_name: string;
  aliases?: string[];
  scene_type: string;
  latitude: number;
  longitude: number;
  geocode_precision: string;
  map_source?: string | null;
  map_source_date?: string | null;
  google_maps_link?: string | null;
  coordinate_status: string;
  hero_image?: HeroImage | null;
};

type EvidenceItem = {
  field_group: string;
  field_value: string;
  indicator_name?: string | null;
  source_name: string;
  source_url: string;
  source_date?: string | null;
  source_tier: string;
  evidence_type: string;
  cross_check_status?: string | null;
  assumption_note?: string | null;
};

type LocalizedEvidenceItem = {
  field_group_label?: string;
  indicator_label?: string;
  field_value?: string;
  field_value_original?: string;
  evidence_type_label?: string;
  source_tier_label?: string;
  cross_check_status_label?: string;
};

type InferenceRecord = {
  inferred_field: string;
  inferred_value: string;
  inference_basis: string;
  inference_chain: string;
  inference_confidence: string;
};

type ReviewItem = {
  reason: string;
  next_action: string;
  status: string;
};

type ComplaintSignal = {
  valid_complaint_count: number;
  weighted_complaint_count: number;
  source_count: number;
  category_counts: Record<string, number>;
  pressure_level: string;
  pressure_percentile?: number | null;
  confidence: string;
  period_days: number;
  latest_observed_at?: string | null;
  data_freshness?: string | null;
};

type NetworkSignals = {
  complaints?: ComplaintSignal | null;
  network_validation_priority?: string;
  validation_reasons?: string[];
  next_action?: string | null;
};

type RagCitation = {
  chunk_id: string;
  source_url?: string | null;
  source_name?: string | null;
  source_tier?: string | null;
  source_date?: string | null;
  fetched_at?: string | null;
  raw_evidence_id?: string | null;
  property_id?: string | null;
  field_group?: string | null;
  excerpt: string;
};

type RagPriorityRecommendation = {
  property_id?: string | null;
  property_name: string;
  country?: string | null;
  scene_type?: string | null;
  priority_band: string;
  evidence_status?: string | null;
  action_class?: string | null;
  recommended_solution?: string | null;
  rationale: string;
  review_next_actions: string[];
};

type RagQueryResponse = {
  answer: string;
  citations: RagCitation[];
  priority_recommendations: RagPriorityRecommendation[];
  review_actions: string[];
};

type SitePacket = {
  entity: Entity;
  scene: {
    annual_visits_est?: number | null;
    proxy_level?: string | null;
    area_metric_name?: string | null;
    proxy_basis?: string | null;
  };
  evidence: EvidenceItem[];
  build_status: {
    indoor_system_presence: string;
    indoor_system_type: string;
    indoor_rat: string;
    build_evidence_status: string;
  };
  demand?: {
    busy_hour_traffic_gb?: number | null;
    busy_hour_bandwidth_mbps?: number | null;
    busy_hour_users?: number | null;
  } | null;
  network_signals?: NetworkSignals | null;
  inference: InferenceRecord[];
  conclusion: {
    evidence_status: string;
    value_class: string;
    action_class: string;
    recommended_solution: string;
    reason_to_recommend: string;
    next_action: string;
  };
  review_queue: ReviewItem[];
  localized?: {
    locale: string;
    entity?: {
      display_name?: string;
      original_name?: string;
      scene_label?: string;
    };
    scene?: {
      scene_type_label?: string;
      area_metric_label?: string;
      proxy_level_label?: string;
    };
    primary_metric?: {
      display_text?: string;
      original_text?: string;
      localization_status?: string;
      source_locale?: string;
      field_group?: string;
      indicator_name?: string | null;
    };
    build_status?: Record<string, string | undefined>;
    conclusion?: {
      evidence_status_label?: string;
      value_class_label?: string;
      action_class_label?: string;
      recommended_solution_label?: string;
      reason_to_recommend?: string;
      reason_to_recommend_original?: string;
      next_action?: string;
      next_action_original?: string;
    };
    evidence?: LocalizedEvidenceItem[];
    inference?: Array<Record<string, string | undefined>>;
    review_queue?: Array<Record<string, string | undefined>>;
  };
};

type MapFeature = {
  type: "Feature";
  geometry: {
    type: "Point";
    coordinates: [number, number];
  };
  properties: {
    property_id: string;
    property_name: string;
    country: string;
    city: string;
    city_id?: string | null;
    city_assignment?: Entity["city_assignment"];
    scene_type: string;
    evidence_status: string;
    value_class: string;
    action_class: string;
    recommended_solution: string;
    main_metric_text: string;
    annual_visits_est?: number | null;
    busy_hour_traffic_gb?: number | null;
    review_count: number;
    source_count: number;
    indoor_system_presence: string;
    indoor_rat: string;
    proxy_level: string;
    google_maps_link?: string | null;
    geocode_precision: string;
    map_source?: string | null;
    coordinate_status: string;
    hero_image_url?: string | null;
    hero_image_alt?: string | null;
    hero_image_source_name?: string | null;
    localized?: Record<string, string | undefined>;
  };
};

type CountryMarker = {
  kind: "country";
  key: string;
  country: string;
  label: string;
  lat: number;
  lng: number;
  positionSource: CountryPositionSource;
  candidateCount: number;
  mapReadyCount: number;
  mappedCityCount: number;
  reviewCount: number;
  sourceCount: number;
  scenes: Record<string, number>;
  propertyIds: string[];
};

type CountryPositionSource = "property_average" | "polygon_centroid" | "display_anchor" | "default_fallback";
type CityPositionSource = "map_ready_average" | "city_geocode" | "property_average";

type CitySummary = {
  country: string;
  city: string;
  city_id?: string | null;
  locality_count?: number;
  candidate_count: number;
  map_point_count: number;
  review_count: number;
  source_count: number;
  scenes: Record<string, number>;
  property_ids: string[];
  lat: number;
  lng: number;
  position_source: CityPositionSource;
};

type CityMarker = {
  kind: "city";
  key: string;
  country: string;
  city: string;
  cityId?: string | null;
  localityCount?: number;
  label: string;
  lat: number;
  lng: number;
  positionSource: CityPositionSource;
  candidateCount: number;
  mapReadyCount: number;
  reviewCount: number;
  sourceCount: number;
  scenes: Record<string, number>;
  propertyIds: string[];
};

type CityClusterMarker = {
  kind: "city_cluster";
  key: string;
  country: string;
  label: string;
  lat: number;
  lng: number;
  cityCount: number;
  candidateCount: number;
  mapReadyCount: number;
  reviewCount: number;
  sourceCount: number;
  scenes: Record<string, number>;
  propertyIds: string[];
  cityKeys: string[];
};

type GlobeMarker = CountryMarker | CityMarker;
type DisplayGlobeMarker = GlobeMarker | CityClusterMarker;

type GeocodeResult = {
  latitude: number;
  longitude: number;
  geocode_precision: string;
  map_source: string;
  city?: string | null;
  country?: string | null;
  display_name?: string | null;
  boundingbox?: number[] | null;
};

type CityCentroidCacheValue = GeocodeResult | "missing" | "loading";

type ScreenGlobeMarker = GlobeMarker & {
  x: number;
  y: number;
  visible: boolean;
  spiderChild?: boolean;
};

type ScreenCityClusterMarker = CityClusterMarker & {
  x: number;
  y: number;
  visible: boolean;
  spiderChild?: boolean;
};

type ScreenDisplayGlobeMarker = ScreenGlobeMarker | ScreenCityClusterMarker;

type PropertiesResponse = {
  candidate_count: number;
  display_count: number;
  packets: SitePacket[];
};

type PropertySearchResult = {
  property_id: string;
  property_name: string;
  matched_name: string;
  match_type: string;
  country: string;
  city: string;
  scene_type: string;
};

type PropertySearchResponse = {
  query: string;
  count: number;
  results: PropertySearchResult[];
};

const propertySearchCache = new Map<string, PropertySearchResponse>();
const propertySearchRequests = new Map<string, Promise<PropertySearchResponse>>();

type CountryPayloadCacheValue = {
  citySummaries: CitySummary[];
  packets: SitePacket[];
};

type PropertyCardViewModel = {
  propertyId: string;
  propertyName: string;
  city: string;
  sceneLabel: string;
  metric: string;
  networkExperience: string;
  valueClass: string;
  actionClass: string;
  actionTone: "primary" | "review" | "watch" | "neutral";
  sourceCount: number;
  sourceLabel: string;
  image?: HeroImage | null;
};

type NetworkExperienceKind = "mobile" | "fixed";

type NetworkExperienceMetric = {
  kind: NetworkExperienceKind;
  label: string;
  downloadMbps: number | null;
  uploadMbps: number | null;
  latencyMs: number | null;
  loadedLatencyDownMs: number | null;
  loadedLatencyUpMs: number | null;
  tests: number | null;
  devices: number | null;
  distanceM: number | null;
  confidence: "high" | "medium" | "low" | "unknown";
  fieldValue: string;
  sourceName: string;
  sourceUrl: string;
  sourceDate?: string | null;
};

type NetworkExperienceSummary = {
  mobile?: NetworkExperienceMetric;
  fixed?: NetworkExperienceMetric;
};

type CountrySummary = {
  country: string;
  candidate_count: number;
  map_point_count: number;
  coordinate_review_count: number;
  review_count: number;
  source_count: number;
  scenes: Record<string, number>;
  scan_maturity?: ScanMaturity;
};

type ScanMaturityLevel = "seed_scan" | "deep_expansion" | "near_saturation";

type ScanMaturity = {
  level: ScanMaturityLevel;
  basis: "active_coverage" | "tracked_rounds" | "tracked_exhaustion";
  scene_count: number;
  evidence_per_candidate: number;
  progress_group_count: number;
  exhausted_group_count: number;
  tracked_rounds: number;
};

type RegionSummary = {
  region: string;
  country_count: number;
  candidate_count: number;
  map_point_count: number;
  review_count: number;
  source_count: number;
  scenes: Record<string, number>;
};

type DiscoveryStatus = {
  task_backlog: Record<string, number>;
  pending_evidence_count: number;
};

type CountryFeature = Feature<Geometry, { name?: string }>;

type Toast = {
  id: number;
  message: string;
  tone: "info" | "success" | "error";
};

type LocaleTransition = {
  id: number;
  targetLocale: string;
  startedAt: number;
};

type RuntimeConfig = {
  mode: string;
  features: {
    exports: boolean;
    rag: boolean;
    connectors: boolean;
    geocode: boolean;
  };
  map: {
    satelliteTileTemplate: string;
    satelliteTileSize: number;
    satelliteAttribution: string;
    propertyOverlayTemplate?: string;
    footfallProvider?: {
      provider: string;
      configured: boolean;
      requiresApiKey: boolean;
      endpointConfigured?: boolean;
    };
  };
  localization?: {
    defaultLocale: string;
    supportedLocales: string[];
  };
  auth?: {
    enabled: boolean;
    guestClickLimit: number;
    usernameHint: string;
  };
  serviceRequests?: {
    enabled: boolean;
    dailyLimit: number;
    types: string[];
    updatesLimit: number;
  };
};

type AuthSession = {
  enabled: boolean;
  authenticated: boolean;
  username?: string | null;
  guestClickLimit: number;
  usernameHint: string;
};

type LocalizationPayload = {
  locale: string;
  default_locale: string;
  fallback_locale: string;
  supported_locales: string[];
  labels: Record<string, any>;
  fallback_labels: Record<string, any>;
};

type ViewMode = "overview" | "country" | "city" | "property";
type WorkspaceListMode = "card" | "dense";
type PropertyOverlayMode = "satellite" | "mobile_network" | "footfall";

type EvidenceGapSummary = {
  missingPrimaryMetricCount: number;
  missingImageCount: number;
  lowEvidenceCount: number;
  coordinateReviewCount: number;
  reviewQueueCount: number;
};

type GlobeProjectionMethods = {
  pointOfView: () => { lat: number; lng: number; altitude: number };
  getScreenCoords: (lat: number, lng: number, altitude?: number) => { x: number; y: number };
};

const DEFAULT_RUNTIME_CONFIG: RuntimeConfig = {
  mode: "local",
  features: {
    exports: true,
    rag: true,
    connectors: true,
    geocode: true,
  },
  map: {
    satelliteTileTemplate: "/map/satellite-tiles/{z}/{y}/{x}",
    satelliteTileSize: 512,
    satelliteAttribution: "Source: VersaTiles Satellite",
    propertyOverlayTemplate: "/map/property-overlays/{property_id}?layer={layer}&radius_m={radius_m}",
    footfallProvider: {
      provider: "public_open_data",
      configured: true,
      requiresApiKey: false,
      endpointConfigured: false,
    },
  },
  localization: {
    defaultLocale: "en",
    supportedLocales: ["en", "zh"],
  },
  auth: {
    enabled: false,
    guestClickLimit: 10,
    usernameHint: "visitor",
  },
  serviceRequests: {
    enabled: true,
    dailyLimit: 10,
    types: ["scan_enhancement", "feature_request", "ppt_report"],
    updatesLimit: 30,
  },
};

const SCENE_LABELS: Record<string, string> = {
  airport_terminal: "Airport",
  convention_center: "Convention",
  stadium: "Stadium",
  luxury_hotel_mice: "Hotel / MICE",
  mall_mixed_use: "Mall / Mixed-use",
  office_government: "Office / Government",
  hospital: "Hospital",
  university: "University",
  transport_hub: "Transport hub",
  cruise_port: "Cruise port",
  mosque: "Mosque",
};

const DEFAULT_LOCALIZATION: LocalizationPayload = {
  locale: "en",
  default_locale: "en",
  fallback_locale: "en",
  supported_locales: ["en", "zh"],
  labels: {
    fallback: {
      unknown: "Unknown",
      no_rows: "No rows",
      no_primary_metric: "No primary metric",
      localization_pending: "Localization pending",
    },
    scenes: SCENE_LABELS,
    enums: {},
    ui: {
      opportunity_globe: "Opportunity Globe",
      loading: "Loading",
      countries: "Countries",
      cities: "Cities",
      candidates: "Candidates",
      sources: "Evidence",
      review: "Review",
      action: "Action",
      primary_metric: "Primary metric",
      network_experience: "Network Experience",
      ookla_tile_proxy: "Ookla tile proxy",
      network_experience_proxy_note: "Tile-level experience proxy, not indoor DAS/build evidence.",
      network_complaints: "Network complaints",
      property_complaint_signals: "Property-level public signals",
      complaint_signal_note: "Aggregated public network complaints only. This does not prove indoor DAS/build status.",
      complaint_empty: "No compliant property-level network complaints are currently available.",
      valid_complaints: "Valid complaints",
      complaint_sources: "Complaint sources",
      complaint_pressure: "Pressure",
      complaint_period: "Observation window",
      complaint_latest: "Latest observation",
      complaint_categories: "Complaint categories",
      complaint_period_days: "{days} days",
      complaint_no_signal: "No signal",
      complaint_weak_coverage: "Weak coverage",
      complaint_slow_data: "Slow data",
      complaint_dropped_call: "Dropped calls",
      complaint_outage: "Network outage",
      complaint_insufficient: "Insufficient",
      mobile_network: "Mobile",
      fixed_network: "Fixed",
      download_mbps: "Download Mbps",
      upload_mbps: "Upload Mbps",
      latency_ms: "Latency ms",
      samples: "Samples",
      tile_distance: "Tile distance",
      tests_devices: "{tests} tests / {devices} devices",
      high_confidence: "High confidence",
      medium_confidence: "Medium confidence",
      low_confidence: "Low confidence",
      scene: "Scene",
      localization_loading: "Loading localized text",
      localization_loading_body: "Refreshing country, city, and property copy.",
      guest_access: "Guest access",
      guest_clicks_remaining: "{count} clicks left",
      sign_in: "Sign in",
      sign_out: "Sign out",
      signed_in_as: "Signed in as {username}",
      login_required: "Login required",
      login_required_body: "Guest access includes {limit} clicks. Sign in to continue.",
      username: "Username",
      password: "Password",
      invalid_credentials: "Invalid username or password",
      close: "Close",
    },
  },
  fallback_labels: {},
};

type ScenePrimaryMetricFilter = {
  label: string;
  indicators: string[];
};

const SCENE_PRIMARY_METRIC_FILTERS: Record<string, ScenePrimaryMetricFilter> = {
  airport_terminal: {
    label: "Passengers",
    indicators: [
      "annual_passenger_throughput",
      "annual_passengers",
      "annual_visits",
      "passenger_throughput",
      "passengers",
      "passenger_count",
      "passenger_movement",
      "passenger_movements",
      "terminal_capacity",
      "passenger_capacity",
    ],
  },
  convention_center: {
    label: "Area / capacity",
    indicators: [
      "exhibition_area",
      "meeting_area",
      "meeting_ballroom_area",
      "ballroom_capacity",
      "peak_event_capacity",
      "plenary_capacity",
      "annual_events",
    ],
  },
  stadium: {
    label: "Seats",
    indicators: ["seat_count", "seats", "capacity", "peak_event_capacity"],
  },
  luxury_hotel_mice: {
    label: "Rooms",
    indicators: ["keys", "room_count", "rooms", "guest_rooms"],
  },
  mall_mixed_use: {
    label: "GLA / footfall",
    indicators: [
      "gla",
      "nla",
      "retail_gfa",
      "mixed_use_area",
      "annual_footfall",
      "annual_visits",
      "footfall",
    ],
  },
  office_government: {
    label: "Area / floors",
    indicators: [
      "office_nla",
      "office_gfa",
      "floor_count",
      "tower_height",
      "site_area",
    ],
  },
  hospital: {
    label: "Beds",
    indicators: ["beds", "bed_count", "outpatient_volume"],
  },
  university: {
    label: "Enrollment",
    indicators: ["enrollment", "campus_population"],
  },
  transport_hub: {
    label: "Ridership",
    indicators: [
      "daily_ridership",
      "interchange_volume",
      "passenger_throughput",
      "annual_passenger_throughput",
    ],
  },
  cruise_port: {
    label: "Passengers",
    indicators: ["passenger_throughput", "annual_passenger_throughput"],
  },
  mosque: {
    label: "Area / visitors",
    indicators: [
      "mosque_area",
      "gross_floor_area",
      "prayer_hall_area",
      "site_area",
      "built_up_area",
      "annual_visitors",
      "annual_visits",
      "daily_visitors",
      "annual_footfall",
      "footfall",
    ],
  },
};

type TargetRegion =
  | "Latin America"
  | "Asia Pacific"
  | "Europe"
  | "Middle East & Central Asia"
  | "Africa";

const TARGET_REGION_ORDER: TargetRegion[] = [
  "Latin America",
  "Asia Pacific",
  "Europe",
  "Middle East & Central Asia",
  "Africa",
];

const REGION_DISPLAY_LABELS: Record<string, string> = {
  "Middle East & Central Asia": "ME & C. Asia",
};
const GUEST_CLICK_STORAGE_KEY = "isite2_guest_click_count";
const UPDATES_LAST_SEEN_STORAGE_KEY = "isite2_updates_last_seen";

const REGION_COUNTRIES: Record<TargetRegion, string[]> = {
  "Latin America": [
    "Antigua and Barbuda",
    "Argentina",
    "Bahamas",
    "Barbados",
    "Belize",
    "Bolivia",
    "Brazil",
    "Chile",
    "Colombia",
    "Costa Rica",
    "Cuba",
    "Dominica",
    "Dominican Republic",
    "Ecuador",
    "El Salvador",
    "Grenada",
    "Guatemala",
    "Guyana",
    "Haiti",
    "Honduras",
    "Jamaica",
    "Mexico",
    "Nicaragua",
    "Panama",
    "Paraguay",
    "Peru",
    "Saint Kitts and Nevis",
    "Saint Lucia",
    "Saint Vincent and the Grenadines",
    "Suriname",
    "Trinidad and Tobago",
    "Uruguay",
    "Venezuela",
  ],
  "Asia Pacific": [
    "Australia",
    "Bangladesh",
    "Bhutan",
    "Brunei",
    "Cambodia",
    "China",
    "Fiji",
    "India",
    "Indonesia",
    "Japan",
    "Kiribati",
    "Laos",
    "Malaysia",
    "Maldives",
    "Marshall Islands",
    "Micronesia",
    "Mongolia",
    "Myanmar",
    "Nauru",
    "Nepal",
    "New Zealand",
    "Pakistan",
    "Palau",
    "Papua New Guinea",
    "Philippines",
    "Samoa",
    "Singapore",
    "Solomon Islands",
    "South Korea",
    "Sri Lanka",
    "Taiwan",
    "Thailand",
    "Timor-Leste",
    "Tonga",
    "Tuvalu",
    "Vanuatu",
    "Vietnam",
  ],
  Europe: [
    "Albania",
    "Bosnia and Herzegovina",
    "Bulgaria",
    "Croatia",
    "Cyprus",
    "Czech Republic",
    "France",
    "Germany",
    "Greece",
    "Moldova",
    "Montenegro",
    "North Macedonia",
    "Serbia",
    "Slovakia",
    "Slovenia",
    "Switzerland",
  ],
  "Middle East & Central Asia": [
    "Afghanistan",
    "Armenia",
    "Azerbaijan",
    "Bahrain",
    "Georgia",
    "Iran",
    "Iraq",
    "Israel",
    "Jordan",
    "Kazakhstan",
    "Kuwait",
    "Kyrgyzstan",
    "Lebanon",
    "Oman",
    "Palestine",
    "Qatar",
    "Saudi Arabia",
    "Syria",
    "Tajikistan",
    "Turkey",
    "Turkmenistan",
    "United Arab Emirates",
    "Uzbekistan",
    "Yemen",
  ],
  Africa: [
    "Algeria",
    "Angola",
    "Benin",
    "Botswana",
    "Burkina Faso",
    "Burundi",
    "Cabo Verde",
    "Cape Verde",
    "Cameroon",
    "Central African Republic",
    "Chad",
    "Comoros",
    "Congo",
    "Cote d'Ivoire",
    "Democratic Republic of the Congo",
    "Djibouti",
    "Egypt",
    "Equatorial Guinea",
    "Eritrea",
    "Eswatini",
    "Ethiopia",
    "Gabon",
    "Gambia",
    "Ghana",
    "Guinea",
    "Guinea-Bissau",
    "Kenya",
    "Lesotho",
    "Liberia",
    "Libya",
    "Madagascar",
    "Malawi",
    "Mali",
    "Mauritania",
    "Mauritius",
    "Morocco",
    "Mozambique",
    "Namibia",
    "Niger",
    "Nigeria",
    "Reunion",
    "Rwanda",
    "Sao Tome and Principe",
    "Senegal",
    "Seychelles",
    "Sierra Leone",
    "Somalia",
    "South Africa",
    "South Sudan",
    "Sudan",
    "Tanzania",
    "Togo",
    "Tunisia",
    "Uganda",
    "Zambia",
    "Zimbabwe",
  ],
};
const REGION_BY_COUNTRY = new Map(
  Object.entries(REGION_COUNTRIES).flatMap(([region, countries]) =>
    countries.map((country) => [normalizeCountryName(country), region] as const),
  ),
);

const COUNTRY_POLYGON_ALIASES = [
  { country: "Bosnia and Herzegovina", polygonCountry: "Bosnia and Herz." },
  { country: "Central African Republic", polygonCountry: "Central African Rep." },
  { country: "Czech Republic", polygonCountry: "Czechia" },
  { country: "Democratic Republic of the Congo", polygonCountry: "Dem. Rep. Congo" },
  { country: "Dominican Republic", polygonCountry: "Dominican Rep." },
  { country: "North Macedonia", polygonCountry: "Macedonia" },
] as const;

const COUNTRY_POLYGON_ALIAS_BY_COUNTRY = new Map(
  COUNTRY_POLYGON_ALIASES.map(({ country, polygonCountry }) => [
    normalizeCountryName(country),
    polygonCountry,
  ] as const),
);

const COUNTRY_DISPLAY_ANCHORS: Record<string, { lat: number; lng: number }> = {
  [normalizeCountryName("Barbados")]: { lat: 13.1939, lng: -59.5432 },
  [normalizeCountryName("Cabo Verde")]: { lat: 16.5388, lng: -23.0418 },
  [normalizeCountryName("Cape Verde")]: { lat: 16.5388, lng: -23.0418 },
  [normalizeCountryName("Maldives")]: { lat: 3.2028, lng: 73.2207 },
  [normalizeCountryName("Seychelles")]: { lat: -4.6796, lng: 55.492 },
  [normalizeCountryName("Sao Tome and Principe")]: { lat: 0.1864, lng: 6.6131 },
  [normalizeCountryName("Comoros")]: { lat: -11.6455, lng: 43.3333 },
  [normalizeCountryName("Mauritius")]: { lat: -20.3484, lng: 57.5522 },
  [normalizeCountryName("Bahrain")]: { lat: 26.0667, lng: 50.5577 },
  [normalizeCountryName("Singapore")]: { lat: 1.3521, lng: 103.8198 },
  [normalizeCountryName("France")]: { lat: 46.2276, lng: 2.2137 },
  [normalizeCountryName("Switzerland")]: { lat: 46.8182, lng: 8.2275 },
  [normalizeCountryName("Albania")]: { lat: 41.1533, lng: 20.1683 },
  [normalizeCountryName("Bosnia and Herzegovina")]: { lat: 43.9159, lng: 17.6791 },
  [normalizeCountryName("Bulgaria")]: { lat: 42.7339, lng: 25.4858 },
  [normalizeCountryName("Croatia")]: { lat: 45.1, lng: 15.2 },
  [normalizeCountryName("Cyprus")]: { lat: 35.1264, lng: 33.4299 },
  [normalizeCountryName("Moldova")]: { lat: 47.4116, lng: 28.3699 },
  [normalizeCountryName("Montenegro")]: { lat: 42.7087, lng: 19.3744 },
  [normalizeCountryName("North Macedonia")]: { lat: 41.6086, lng: 21.7453 },
  [normalizeCountryName("Serbia")]: { lat: 44.0165, lng: 21.0059 },
  [normalizeCountryName("Slovakia")]: { lat: 48.669, lng: 19.699 },
  [normalizeCountryName("Slovenia")]: { lat: 46.1512, lng: 14.9955 },
  [normalizeCountryName("Reunion")]: { lat: -21.1151, lng: 55.5364 },
};

const ASSET_BASE_URL = import.meta.env.BASE_URL;
const EARTH_IMAGE = `${ASSET_BASE_URL}earth-blue-marble.jpg`;
const EARTH_BUMP = `${ASSET_BASE_URL}earth-topology.png`;
const OVERVIEW_ALTITUDE = 1.42;
const OVERVIEW_ROTATE_SPEED = 0.24;
const CITY_CLUSTER_RADIUS_PX = 84;
const SPIDER_BASE_RADIUS_PX = 78;
const SPIDER_RING_GAP_PX = 44;
const SPIDER_FIRST_RING_CAPACITY = 8;
const SPIDER_EDGE_PADDING_PX = 56;
const CARD_RENDER_BATCH_SIZE = 24;
const CARD_RENDER_BATCH_DELAY_MS = 80;
const CARD_IMAGE_ROOT_MARGIN = "360px 0px";
const LOCALIZATION_THINKING_MIN_MS = 520;

type GlobeMode = "overview" | "focused";
type GlobeControlState = {
  autoRotate: boolean;
  autoRotateSpeed: number;
  enableDamping: boolean;
};

type IdleSchedulerWindow = Window & {
  requestIdleCallback?: (callback: () => void, options?: { timeout?: number }) => number;
  cancelIdleCallback?: (handle: number) => void;
};

function initialPointOfViewOverride(): { lat: number; lng: number } | null {
  const value = new URLSearchParams(window.location.search).get("test_pov");
  if (!value) {
    return null;
  }
  const [lat, lng] = value.split(",").map((item) => Number(item.trim()));
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    return null;
  }
  return { lat, lng };
}

function App() {
  const globeRef = useRef<GlobeMethods | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const loadSequenceRef = useRef(0);
  const pendingPropertySelectionRef = useRef("");
  const pendingServiceRequestOpenRef = useRef(false);
  const previousDataScopeKeyRef = useRef("");
  const countrySummaryCacheRef = useRef<CountrySummary[] | null>(null);
  const countryPayloadCacheRef = useRef<Map<string, CountryPayloadCacheValue>>(new Map());
  const globeModeRef = useRef<GlobeMode>("overview");
  const pointOfViewOverrideRef = useRef<{ lat: number; lng: number } | null>(
    initialPointOfViewOverride(),
  );
  const [stageSize, setStageSize] = useState({ width: 900, height: 720 });
  const [globeReady, setGlobeReady] = useState(false);
  const [features, setFeatures] = useState<MapFeature[]>([]);
  const [packets, setPackets] = useState<SitePacket[]>([]);
  const [summaries, setSummaries] = useState<CountrySummary[]>([]);
  const [citySummaries, setCitySummaries] = useState<CitySummary[]>([]);
  const [selectedCountry, setSelectedCountry] = useState<string>("");
  const [selectedCityKey, setSelectedCityKey] = useState<string>("");
  const [hoveredCountry, setHoveredCountry] = useState<string>("");
  const [globePointerPosition, setGlobePointerPosition] = useState<MapPointerPosition | null>(null);
  const [globeHoveredMarker, setGlobeHoveredMarker] = useState<MapHoverMarker | null>(null);
  const [selectedPropertyId, setSelectedPropertyId] = useState<string>("");
  const [isCountryScopeOpen, setIsCountryScopeOpen] = useState(false);
  const [countryQuery, setCountryQuery] = useState("");
  const [isRagOpen, setIsRagOpen] = useState(false);
  const [ragQuestion, setRagQuestion] = useState("");
  const [ragResult, setRagResult] = useState<RagQueryResponse | null>(null);
  const [ragLoading, setRagLoading] = useState(false);
  const [ragError, setRagError] = useState("");
  const [sceneFilter, setSceneFilter] = useState("");
  const [panelSceneFilter, setPanelSceneFilter] = useState("");
  const [overviewRegionFilter, setOverviewRegionFilter] = useState("");
  const [evidenceFilter, setEvidenceFilter] = useState("");
  const [actionFilter, setActionFilter] = useState("");
  const [reviewOnly, setReviewOnly] = useState(false);
  const [listMode, setListMode] = useState<WorkspaceListMode>("card");
  const [propertyOverlayMode, setPropertyOverlayMode] = useState<PropertyOverlayMode>("satellite");
  const [expandedClusterKey, setExpandedClusterKey] = useState("");
  const [screenGlobeMarkers, setScreenGlobeMarkers] = useState<ScreenDisplayGlobeMarker[]>([]);
  const [cityCentroidCache, setCityCentroidCache] = useState<Map<string, CityCentroidCacheValue>>(
    () => new Map(),
  );
  const [detailTab, setDetailTab] = useState<"evidence" | "inference" | "review">("evidence");
  const [loading, setLoading] = useState(true);
  const [packetsLoading, setPacketsLoading] = useState(true);
  const [countryLoadFailed, setCountryLoadFailed] = useState(false);
  const [countryReloadToken, setCountryReloadToken] = useState(0);
  const [discoveryStatus, setDiscoveryStatus] = useState<DiscoveryStatus | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);
  const [countryExportLoading, setCountryExportLoading] = useState(false);
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfig>(DEFAULT_RUNTIME_CONFIG);
  const [locale, setLocale] = useState(DEFAULT_RUNTIME_CONFIG.localization?.defaultLocale || "en");
  const [localization, setLocalization] = useState<LocalizationPayload>(DEFAULT_LOCALIZATION);
  const [localeTransition, setLocaleTransition] = useState<LocaleTransition | null>(null);
  const [authSession, setAuthSession] = useState<AuthSession>({
    enabled: false,
    authenticated: false,
    username: null,
    guestClickLimit: DEFAULT_RUNTIME_CONFIG.auth?.guestClickLimit || 10,
    usernameHint: DEFAULT_RUNTIME_CONFIG.auth?.usernameHint || "visitor",
  });
  const [guestClickCount, setGuestClickCount] = useState(() => readStoredGuestClickCount());
  const guestClickCountRef = useRef(guestClickCount);
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [loginUsername, setLoginUsername] = useState(DEFAULT_RUNTIME_CONFIG.auth?.usernameHint || "visitor");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginLoading, setLoginLoading] = useState(false);
  const [loginError, setLoginError] = useState("");
  const [serviceRequestOpen, setServiceRequestOpen] = useState(false);
  const [updatesOpen, setUpdatesOpen] = useState(false);
  const [updates, setUpdates] = useState<ProductUpdate[]>([]);
  const [updatesLoading, setUpdatesLoading] = useState(false);
  const [updatesError, setUpdatesError] = useState("");
  const [updatesLastSeen, setUpdatesLastSeen] = useState(() => readStoredUpdatesLastSeen());

  const useMockGlobe = useMemo(() => new URLSearchParams(window.location.search).has("mock_globe"), []);
  const useMockCluster = useMemo(() => new URLSearchParams(window.location.search).has("mock_cluster"), []);
  const useMockSatellite = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    return params.has("mock_satellite") || (params.has("mock_globe") && !params.has("real_satellite"));
  }, []);
  const enabledFeatures = runtimeConfig.features;
  const authConfig = authConfigFromRuntime(runtimeConfig);
  const authGateEnabled = authConfig.enabled;
  const authenticated = authGateEnabled && authSession.authenticated;
  const guestClickLimit = authConfig.guestClickLimit;
  const guestClicksRemaining = Math.max(0, guestClickLimit - guestClickCount);
  const serviceRequestsEnabled = runtimeConfig.serviceRequests?.enabled !== false;
  const updateUnreadCount = updates.filter(
    (update) => new Date(update.published_at).getTime() > updatesLastSeen,
  ).length;
  const countryPolygons = useMemo(() => loadCountryPolygons(), []);
  const countryDisplayPositionCache = useMemo(
    () => buildCountryDisplayPositionCache(countryPolygons),
    [countryPolygons],
  );
  const countryNamesWithData = useMemo(
    () => new Set(summaries.map((summary) => summary.country)),
    [summaries],
  );
  const selectedSummary = useMemo(
    () => aggregateSummary(summaries, selectedCountry),
    [summaries, selectedCountry],
  );
  const regionSummaries = useMemo(
    () => aggregateRegionSummaries(summaries),
    [summaries],
  );
  const selectedOverviewRegionSummary = useMemo(
    () => regionSummaries.find((region) => region.region === overviewRegionFilter) ?? null,
    [overviewRegionFilter, regionSummaries],
  );
  const countryMarkers = useMemo(
    () => aggregateCountryMarkers(summaries, features, countryDisplayPositionCache),
    [countryDisplayPositionCache, features, summaries],
  );
  const overviewRegionCountrySet = useMemo(
    () => overviewRegionFilter
      ? new Set(
          summaries
            .filter((summary) => regionForCountry(summary.country) === overviewRegionFilter)
            .map((summary) => summary.country),
        )
      : null,
    [overviewRegionFilter, summaries],
  );
  const overviewCountryMarkers = useMemo(
    () => overviewRegionCountrySet
      ? countryMarkers.filter((marker) => overviewRegionCountrySet.has(marker.country))
      : countryMarkers,
    [countryMarkers, overviewRegionCountrySet],
  );
  const metricFilteredPackets = packets;
  const cityMarkers = useMemo(
    () => {
      const summaryMarkers = aggregateCitySummaryMarkers(citySummaries);
      if (!selectedCountry) {
        return summaryMarkers;
      }
      const packetMarkers = aggregateCityMarkers(metricFilteredPackets, features, cityCentroidCache);
      return packetMarkers.length > 0 ? packetMarkers : summaryMarkers;
    },
    [cityCentroidCache, citySummaries, features, metricFilteredPackets, selectedCountry],
  );
  const selectedCountryCityCount = useMemo(
    () => selectedCountry
      ? cityMarkers.filter((marker) => marker.country === selectedCountry).length
      : 0,
    [cityMarkers, selectedCountry],
  );
  const overviewCountryCount = selectedOverviewRegionSummary?.country_count ?? summaries.length;
  const visibleGlobeMarkers = useMemo<GlobeMarker[]>(
    () => selectedCountry
      ? cityMarkers.filter((marker) => marker.country === selectedCountry)
      : overviewCountryMarkers,
    [cityMarkers, overviewCountryMarkers, selectedCountry],
  );
  const mockScreenGlobeMarkers = useMemo<ScreenDisplayGlobeMarker[]>(() => {
    if (!useMockGlobe || !useMockCluster) {
      return [];
    }
    const projected = projectMockGlobeMarkers(
      visibleGlobeMarkers,
      selectedCityKey,
      stageSize,
      pointOfViewOverrideRef.current,
    );
    return clusterScreenGlobeMarkers(projected, {
      expandedClusterKey,
      selectedCityKey,
      selectedCountry,
      stageSize,
    });
  }, [
    expandedClusterKey,
    selectedCityKey,
    selectedCountry,
    stageSize,
    useMockCluster,
    useMockGlobe,
    visibleGlobeMarkers,
  ]);
  const selectedCityMarker = useMemo(
    () => cityMarkers.find((marker) => marker.key === selectedCityKey) ?? null,
    [cityMarkers, selectedCityKey],
  );
  const visiblePackets = useMemo(() => {
    if (!selectedCityKey) {
      return metricFilteredPackets;
    }
    return metricFilteredPackets.filter(
      (packet) => cityKey(
        packet.entity.country,
        packet.entity.city,
        packet.entity.city_assignment?.city_id,
      ) === selectedCityKey,
    );
  }, [metricFilteredPackets, selectedCityKey]);
  const panelPackets = useMemo(
    () => filterAndSortPanelPackets(visiblePackets, panelSceneFilter),
    [panelSceneFilter, visiblePackets],
  );
  const panelSummary = useMemo(
    () => {
      if (selectedCityKey) {
        return summaryFromPackets(visiblePackets, selectedCityMarker);
      }
      if (selectedCountry) {
        return {
          ...summaryFromPackets(metricFilteredPackets, null),
          scan_maturity: selectedSummary.scan_maturity,
        };
      }
      return selectedOverviewRegionSummary
        ? summaryFromRegionSummary(selectedOverviewRegionSummary)
        : selectedSummary;
    },
    [
      metricFilteredPackets,
      selectedCityKey,
      selectedCityMarker,
      selectedOverviewRegionSummary,
      selectedCountry,
      selectedSummary,
      visiblePackets,
    ],
  );
  const selectedPacket = useMemo(
    () => metricFilteredPackets.find((packet) => packet.entity.property_id === selectedPropertyId) ?? null,
    [metricFilteredPackets, selectedPropertyId],
  );
  const viewMode = deriveViewMode(selectedCountry, selectedCityKey, selectedPropertyId);
  const overviewRegionFocus = useMemo(
    () => selectedOverviewRegionSummary
      ? globeFocusFromMarkers(overviewCountryMarkers)
      : null,
    [overviewCountryMarkers, selectedOverviewRegionSummary],
  );
  const gapSummary = useMemo(
    () => summarizeEvidenceGaps(panelPackets),
    [panelPackets],
  );
  const geoVisualMode = deriveGeoVisualMode(viewMode);
  const satelliteCamera = useMemo(
    () => satelliteCameraForScope({
      mode: geoVisualMode,
      selectedCountry,
      selectedCityMarker,
      selectedPacket,
      cityMarkers,
      visiblePackets,
      metricFilteredPackets,
      features,
      countryPolygons,
    }),
    [
      cityMarkers,
      countryPolygons,
      features,
      geoVisualMode,
      metricFilteredPackets,
      selectedCityMarker,
      selectedCountry,
      selectedPacket,
      visiblePackets,
    ],
  );
  const satelliteMarkers = useMemo(
    () => satelliteMarkersForScope({
      mode: geoVisualMode,
      selectedCountry,
      selectedCityKey,
      selectedPropertyId,
      selectedPacket,
      cityMarkers,
      visiblePackets,
      metricFilteredPackets,
    }),
    [
      cityMarkers,
      geoVisualMode,
      metricFilteredPackets,
      selectedCityKey,
      selectedCountry,
      selectedPacket,
      selectedPropertyId,
      visiblePackets,
    ],
  );

  const workspaceContext = useMemo(
    () => workspaceContextLabel({
      selectedCountry,
      selectedCityMarker,
      panelSceneFilter,
      sceneFilter,
      evidenceFilter,
      actionFilter,
      reviewOnly,
      localization,
    }),
    [
      actionFilter,
      evidenceFilter,
      localization,
      panelSceneFilter,
      reviewOnly,
      sceneFilter,
      selectedCityMarker,
      selectedCountry,
    ],
  );
  const globeMode: GlobeMode = viewMode === "overview" && !overviewRegionFilter ? "overview" : "focused";
  globeModeRef.current = globeMode;
  const overviewProjectedMarkerCount = useMockGlobe
    ? useMockCluster
      ? mockScreenGlobeMarkers.length
      : visibleGlobeMarkers.length
    : screenGlobeMarkers.length;
  const overviewSummaryReady = !loading;
  const overviewRegionReady = overviewSummaryReady && (summaries.length === 0 || regionSummaries.length > 0);
  const overviewMarkersReady = overviewSummaryReady
    && (visibleGlobeMarkers.length === 0 || overviewProjectedMarkerCount > 0);
  const initialOverviewReady = viewMode !== "overview"
    || (globeReady && overviewSummaryReady && overviewRegionReady && overviewMarkersReady);
  const showInitialThinking = viewMode === "overview" && !initialOverviewReady;
  const aiThinkingStage = aiThinkingStageLabel({
    globeReady,
    overviewMarkersReady,
    overviewRegionReady,
    overviewSummaryReady,
  });
  const showLocalizationThinking = Boolean(localeTransition);
  const activeThinking = showLocalizationThinking
    ? {
        mode: "localization" as const,
        stage: uiLabel(localization, "ui.localization_loading", "Loading localized text"),
        detail: uiLabel(
          localization,
          "ui.localization_loading_body",
          "Refreshing country, city, and property copy.",
        ),
      }
    : showInitialThinking
      ? {
          mode: "bootstrap" as const,
          stage: aiThinkingStage,
          detail: "Preparing the opportunity globe",
        }
      : null;

  const notify = useCallback((message: string, tone: Toast["tone"] = "info") => {
    const id = Date.now();
    setToast({ id, message, tone });
    window.setTimeout(() => {
      setToast((current) => (current?.id === id ? null : current));
    }, 2800);
  }, []);

  const applyGlobeMode = useCallback((mode: GlobeMode) => {
    globeModeRef.current = mode;
    configureGlobeControls(globeRef.current, mode);
  }, []);

  const handleLocaleSelect = useCallback((nextLocale: string) => {
    const now = window.performance?.now?.() ?? Date.now();
    setLocaleTransition({
      id: Date.now(),
      targetLocale: nextLocale,
      startedAt: now,
    });
    if (nextLocale !== locale) {
      setLocale(nextLocale);
    }
  }, [locale]);

  useEffect(() => {
    let active = true;
    void fetchJson<RuntimeConfig>("/runtime-config")
      .then((config) => {
        if (active) {
          const normalized = normalizeRuntimeConfig(config);
          setRuntimeConfig(normalized);
          setLoginUsername((current) => current || normalized.auth?.usernameHint || "visitor");
          setLocale((current) =>
            current || normalized.localization?.defaultLocale || DEFAULT_LOCALIZATION.locale,
          );
        }
      })
      .catch(() => {
        if (active) {
          setRuntimeConfig(DEFAULT_RUNTIME_CONFIG);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    guestClickCountRef.current = guestClickCount;
  }, [guestClickCount]);

  useEffect(() => {
    if (!authGateEnabled) {
      setAuthSession({
        enabled: false,
        authenticated: false,
        username: null,
        guestClickLimit,
        usernameHint: authConfig.usernameHint,
      });
      return;
    }
    let active = true;
    void fetchJson<AuthSession>("/auth/session")
      .then((session) => {
        if (!active) {
          return;
        }
        setAuthSession(session);
        setLoginUsername((current) => current || session.usernameHint || authConfig.usernameHint);
        if (session.authenticated) {
          guestClickCountRef.current = 0;
          setGuestClickCount(0);
          clearStoredGuestClickCount();
        }
      })
      .catch(() => {
        if (active) {
          setAuthSession({
            enabled: true,
            authenticated: false,
            username: null,
            guestClickLimit,
            usernameHint: authConfig.usernameHint,
          });
        }
      });
    return () => {
      active = false;
    };
  }, [authConfig.usernameHint, authGateEnabled, guestClickLimit]);

  useEffect(() => {
    if (useMockGlobe) {
      setGlobeReady(true);
    }
  }, [useMockGlobe]);

  useEffect(() => {
    let active = true;
    void fetchJson<LocalizationPayload>(`/rules/localization?locale=${encodeURIComponent(locale)}`)
      .then((payload) => {
        if (active) {
          setLocalization(payload);
        }
      })
      .catch(() => {
        if (active) {
          setLocalization({ ...DEFAULT_LOCALIZATION, locale });
        }
      });
    countrySummaryCacheRef.current = null;
    countryPayloadCacheRef.current.clear();
    return () => {
      active = false;
    };
  }, [locale]);

  useEffect(() => {
    if (!serviceRequestsEnabled) {
      return;
    }
    const controller = new AbortController();
    setUpdatesLoading(true);
    setUpdatesError("");
    void fetchJson<{ updates: ProductUpdate[] }>(
      `/updates?locale=${encodeURIComponent(locale)}&limit=${runtimeConfig.serviceRequests?.updatesLimit || 30}`,
      { signal: controller.signal },
    )
      .then((payload) => setUpdates(payload.updates || []))
      .catch((fetchError) => {
        if (!isAbortError(fetchError)) {
          setUpdatesError(uiLabel(localization, "ui.updates_load_error", "Unable to load updates."));
        }
      })
      .finally(() => setUpdatesLoading(false));
    return () => controller.abort();
  }, [locale, localization, runtimeConfig.serviceRequests?.updatesLimit, serviceRequestsEnabled]);

  const askRag = useCallback(async () => {
    if (!enabledFeatures.rag) {
      return;
    }
    const question = ragQuestion.trim();
    if (!question || ragLoading) {
      return;
    }
    setRagLoading(true);
    setRagError("");
    try {
      const context = {
        property_id: selectedPacket?.entity.property_id,
        country: selectedPacket?.entity.country || selectedCountry || undefined,
      };
      await fetchJson("/rag/index", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(context),
      });
      const result = await fetchJson<RagQueryResponse>("/rag/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...context,
          scene_type: selectedPacket?.entity.scene_type || sceneFilter || undefined,
          question,
          top_k: 6,
        }),
      });
      setRagResult(result);
    } catch (error) {
      setRagError(errorMessage(error));
    } finally {
      setRagLoading(false);
    }
  }, [
    enabledFeatures.rag,
    ragLoading,
    ragQuestion,
    sceneFilter,
    selectedCountry,
    selectedPacket,
  ]);

  const loadData = useCallback(async (signal?: AbortSignal) => {
    const sequence = ++loadSequenceRef.current;
    const scopeParams = new URLSearchParams();
    addParam(scopeParams, "country", selectedCountry);
    addParam(scopeParams, "scene_type", sceneFilter);
    addParam(scopeParams, "evidence_status", evidenceFilter);
    addParam(scopeParams, "action_class", actionFilter);
    if (reviewOnly) {
      scopeParams.set("has_review_issue", "true");
    }
    const scopeKey = scopeParams.toString();
    const sameDataScope = previousDataScopeKeyRef.current === scopeKey;
    previousDataScopeKeyRef.current = scopeKey;
    const params = new URLSearchParams(scopeParams);
    addParam(params, "locale", locale);
    const query = params.toString();
    const cachedCountrySummary = countrySummaryCacheRef.current;
    const countryPayloadCacheKey = selectedCountry ? query : "";
    const cachedCountryPayload = countryPayloadCacheKey
      ? countryPayloadCacheRef.current.get(countryPayloadCacheKey)
      : null;
    const shouldPreservePacketsDuringLocaleRefresh = Boolean(
      selectedCountry && sameDataScope && !cachedCountryPayload,
    );
    setCountryLoadFailed(false);
    setLoading(!cachedCountrySummary);
    setPacketsLoading(Boolean(selectedCountry && !cachedCountryPayload));
    setFeatures([]);
    setPackets((current) =>
      cachedCountryPayload?.packets
        || (shouldPreservePacketsDuringLocaleRefresh ? current : []),
    );
    setCitySummaries((current) =>
      cachedCountryPayload?.citySummaries
        || (shouldPreservePacketsDuringLocaleRefresh ? current : []),
    );
    if (cachedCountryPayload || !shouldPreservePacketsDuringLocaleRefresh) {
      setSelectedPropertyId((current) =>
        keepSelectedPropertyIdIfVisible(current, cachedCountryPayload?.packets || []),
      );
    }
    try {
      const countrySummary = cachedCountrySummary
        ? cachedCountrySummary
        : await fetchJson<CountrySummary[]>("/map/country-summary", { signal });
      if (sequence !== loadSequenceRef.current) {
        return;
      }
      if (!countrySummaryCacheRef.current) {
        countrySummaryCacheRef.current = countrySummary || [];
      }
      setSummaries(countrySummaryCacheRef.current);
      setLoading(false);

      if (!selectedCountry) {
        setFeatures([]);
        setPackets([]);
        setCitySummaries([]);
        setSelectedPropertyId("");
        setPacketsLoading(false);
        return;
      }

      if (cachedCountryPayload) {
        setPacketsLoading(false);
        return;
      }

      let citySummaryFailed = false;
      const citySummaryPromise = fetchJson<{ cities: CitySummary[] }>(
        withQuery("/map/city-summary", query),
        { signal },
      ).then((citySummary) => {
        const nextCitySummaries = citySummary.cities || [];
        if (sequence === loadSequenceRef.current && !signal?.aborted) {
          setCitySummaries(nextCitySummaries);
          setLoading(false);
        }
        return nextCitySummaries;
      }).catch((error) => {
        if (signal?.aborted || isAbortError(error)) {
          throw error;
        }
        citySummaryFailed = true;
        return [];
      });

      const properties = await fetchJson<PropertiesResponse>(
        withQuery("/properties", query),
        { signal },
      );
      if (sequence !== loadSequenceRef.current || signal?.aborted) {
        return;
      }
      const nextPackets = properties.packets || [];
      if (properties.candidate_count > 0 && nextPackets.length === 0) {
        throw new Error("Property packet response was incomplete.");
      }
      setPackets(nextPackets);
      setSelectedPropertyId((current) => keepSelectedPropertyIdIfVisible(current, nextPackets));
      setPacketsLoading(false);

      const nextCitySummaries = await citySummaryPromise;
      if (sequence !== loadSequenceRef.current || signal?.aborted) {
        return;
      }
      if (!citySummaryFailed) {
        countryPayloadCacheRef.current.set(countryPayloadCacheKey, {
          citySummaries: nextCitySummaries,
          packets: nextPackets,
        });
      }
    } catch (error) {
      if (signal?.aborted || isAbortError(error)) {
        return;
      }
      if (sequence === loadSequenceRef.current) {
        setCountryLoadFailed(Boolean(selectedCountry));
        notify(errorMessage(error), "error");
        setPacketsLoading(false);
      }
    } finally {
      if (sequence === loadSequenceRef.current && !signal?.aborted) {
        setLoading(false);
      }
    }
  }, [
    actionFilter,
    countryReloadToken,
    evidenceFilter,
    locale,
    notify,
    reviewOnly,
    sceneFilter,
    selectedCountry,
  ]);

  useEffect(() => {
    const element = stageRef.current;
    if (!element) {
      return;
    }
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect) {
        setStageSize({
          width: Math.max(320, Math.floor(rect.width)),
          height: Math.max(360, Math.floor(rect.height)),
        });
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadData(controller.signal);
    return () => controller.abort();
  }, [loadData]);

  useEffect(() => {
    const pendingPropertyId = pendingPropertySelectionRef.current;
    if (!pendingPropertyId) {
      return;
    }
    if (packets.some((packet) => packet.entity.property_id === pendingPropertyId)) {
      setSelectedPropertyId(pendingPropertyId);
      pendingPropertySelectionRef.current = "";
    }
  }, [packets]);

  useEffect(() => {
    if (!localeTransition) {
      return;
    }
    if (
      locale !== localeTransition.targetLocale
      || localization.locale !== localeTransition.targetLocale
      || loading
      || packetsLoading
    ) {
      return;
    }
    const elapsed = (window.performance?.now?.() ?? Date.now()) - localeTransition.startedAt;
    const delay = Math.max(0, LOCALIZATION_THINKING_MIN_MS - elapsed);
    const timeout = window.setTimeout(() => {
      setLocaleTransition((current) =>
        current?.id === localeTransition.id ? null : current,
      );
    }, delay);
    return () => window.clearTimeout(timeout);
  }, [loading, locale, localeTransition, localization.locale, packetsLoading]);

  useEffect(() => {
    applyGlobeMode(globeMode);
  }, [applyGlobeMode, globeMode]);

  useEffect(() => {
    if (viewMode !== "overview" && overviewRegionFilter) {
      setOverviewRegionFilter("");
    }
  }, [overviewRegionFilter, viewMode]);

  useEffect(() => {
    if (overviewRegionFilter && !regionSummaries.some((region) => region.region === overviewRegionFilter)) {
      setOverviewRegionFilter("");
    }
  }, [overviewRegionFilter, regionSummaries]);

  useEffect(() => {
    if (!selectedCityKey || loading) {
      return;
    }
    if (!cityMarkers.some((marker) => marker.key === selectedCityKey)) {
      setSelectedCityKey("");
    }
  }, [cityMarkers, loading, selectedCityKey]);

  useEffect(() => {
    if (!selectedPropertyId) {
      return;
    }
    if (!metricFilteredPackets.some((packet) => packet.entity.property_id === selectedPropertyId)) {
      setSelectedPropertyId("");
    }
  }, [metricFilteredPackets, selectedPropertyId]);

  useEffect(() => {
    if (viewMode !== "property" || !selectedPropertyId) {
      setPropertyOverlayMode("satellite");
    }
  }, [selectedPropertyId, viewMode]);

  useEffect(() => {
    setPanelSceneFilter("");
  }, [selectedCountry, selectedCityKey]);

  useEffect(() => {
    if (!panelSceneFilter) {
      return;
    }
    if (!visiblePackets.some((packet) => packet.entity.scene_type === panelSceneFilter)) {
      setPanelSceneFilter("");
    }
  }, [panelSceneFilter, visiblePackets]);

  useEffect(() => {
    setExpandedClusterKey("");
  }, [
    actionFilter,
    evidenceFilter,
    reviewOnly,
    sceneFilter,
    selectedCityKey,
    selectedCountry,
  ]);

  useEffect(() => {
    if (!selectedCountry || citySummaries.length > 0) {
      return;
    }
    const missingCities = citiesNeedingCentroids(
      metricFilteredPackets,
      features,
      selectedCountry,
      cityCentroidCache,
    );
    if (missingCities.length === 0) {
      return;
    }

    if (!enabledFeatures.geocode) {
      setCityCentroidCache((current) => {
        const next = new Map(current);
        missingCities.forEach((city) => next.set(city.key, "missing"));
        return next;
      });
      return;
    }

    setCityCentroidCache((current) => {
      const next = new Map(current);
      missingCities.forEach((city) => next.set(city.key, "loading"));
      return next;
    });

    missingCities.forEach((city) => {
      const query = `${city.city}, ${city.country}`;
      void fetchJson<GeocodeResult>(`/connectors/geocode?q=${encodeURIComponent(query)}`)
        .then((result) => {
          setCityCentroidCache((current) => {
            const next = new Map(current);
            next.set(city.key, isUsableGeocodeResult(result) ? result : "missing");
            return next;
          });
        })
        .catch(() => {
          setCityCentroidCache((current) => {
            const next = new Map(current);
            next.set(city.key, "missing");
            return next;
          });
        });
    });
  }, [
    cityCentroidCache,
    citySummaries.length,
    enabledFeatures.geocode,
    features,
    metricFilteredPackets,
    selectedCountry,
  ]);

  useEffect(() => {
    configureGlobeControls(globeRef.current, globeMode);
    const interval = window.setInterval(
      () => configureGlobeControls(globeRef.current, globeModeRef.current),
      500,
    );
    return () => window.clearInterval(interval);
  }, [globeMode]);

  useEffect(() => {
    if (useMockGlobe) {
      return;
    }
    let frame = 0;
    const updateMarkerProjection = () => {
      const globe = globeRef.current as unknown as GlobeProjectionMethods | null;
      configureGlobeControls(globeRef.current, globeModeRef.current);
      if (!globe || visibleGlobeMarkers.length === 0) {
        setScreenGlobeMarkers((current) => (current.length === 0 ? current : []));
        frame = window.requestAnimationFrame(updateMarkerProjection);
        return;
      }
      try {
        const projected = projectGlobeMarkers(
          globe,
          visibleGlobeMarkers,
          selectedCityKey,
          pointOfViewOverrideRef.current,
        );
        const displayMarkers = clusterScreenGlobeMarkers(projected, {
          expandedClusterKey,
          selectedCityKey,
          selectedCountry,
          stageSize,
        });
        setScreenGlobeMarkers((current) =>
          sameScreenGlobeMarkers(current, displayMarkers) ? current : displayMarkers,
        );
      } catch {
        setScreenGlobeMarkers((current) => (current.length === 0 ? current : []));
      }
      frame = window.requestAnimationFrame(updateMarkerProjection);
    };
    frame = window.requestAnimationFrame(updateMarkerProjection);
    return () => window.cancelAnimationFrame(frame);
  }, [
    expandedClusterKey,
    selectedCityKey,
    selectedCountry,
    stageSize,
    useMockGlobe,
    visibleGlobeMarkers,
  ]);

  useEffect(() => {
    if (useMockGlobe || viewMode !== "overview") {
      return;
    }
    if (overviewRegionFilter && overviewRegionFocus) {
      configureGlobeControls(globeRef.current, "focused");
      globeRef.current?.pointOfView(
        { ...overviewRegionFocus, altitude: OVERVIEW_ALTITUDE },
        globeReady ? 900 : 0,
      );
      return;
    }
    const override = pointOfViewOverrideRef.current;
    if (override) {
      configureGlobeControls(globeRef.current, "focused");
      globeRef.current?.pointOfView({ ...override, altitude: OVERVIEW_ALTITUDE }, 0);
      return;
    }
    configureGlobeControls(globeRef.current, "overview");
    globeRef.current?.pointOfView({ lat: 12, lng: 16, altitude: OVERVIEW_ALTITUDE }, globeReady ? 900 : 0);
  }, [globeReady, overviewRegionFilter, overviewRegionFocus, useMockGlobe, viewMode]);

  useEffect(() => {
    if (viewMode === "overview" && !initialOverviewReady) {
      return;
    }
    const refreshDiscoveryStatus = async () => {
      try {
        setDiscoveryStatus(await fetchJson<DiscoveryStatus>("/discovery/status"));
      } catch {
        setDiscoveryStatus(null);
      }
    };
    void refreshDiscoveryStatus();
    const interval = window.setInterval(refreshDiscoveryStatus, 30000);
    return () => window.clearInterval(interval);
  }, [initialOverviewReady, viewMode]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsCountryScopeOpen(false);
        setIsRagOpen(false);
        setExpandedClusterKey("");
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  useLayoutEffect(() => {
    window.__isite2SelectCountry = (country: string) => {
      applyGlobeMode(country ? "focused" : "overview");
      setSelectedCountry(country);
      setSelectedCityKey("");
      setSelectedPropertyId("");
      setPanelSceneFilter("");
      setOverviewRegionFilter("");
      setExpandedClusterKey("");
      setIsCountryScopeOpen(false);
      setCountryQuery("");
    };
    window.__isite2GlobeControlState = () => {
      const mode = stageRef.current?.dataset.globeMode === "focused" ? "focused" : "overview";
      configureGlobeControls(globeRef.current, mode);
      const controls = globeRef.current?.controls();
      if (!controls) {
        return {
          autoRotate: mode === "overview",
          autoRotateSpeed: OVERVIEW_ROTATE_SPEED,
          enableDamping: true,
        };
      }
      return {
        autoRotate: controls.autoRotate,
        autoRotateSpeed: controls.autoRotateSpeed,
        enableDamping: controls.enableDamping,
      };
    };
    window.__isite2SetPointOfView = (lat: number, lng: number, altitude = OVERVIEW_ALTITUDE) => {
      pointOfViewOverrideRef.current = { lat, lng };
      globeModeRef.current = "focused";
      configureGlobeControls(globeRef.current, "focused");
      globeRef.current?.pointOfView({ lat, lng, altitude }, 0);
    };
    window.__isite2GlobeAssetState = () => ({
      globeImageUrl: EARTH_IMAGE,
      bumpImageUrl: EARTH_BUMP,
    });
    window.__isite2CountryVisualState = () => {
  const dataCountry = countryPolygons.find((country) =>
        countrySetHasEquivalent(countryNamesWithData, countryName(country)),
      );
      const dataCountryVisualState = dataCountry
        ? countryPolygonVisualState(dataCountry, selectedCountry, hoveredCountry, countryNamesWithData)
        : null;
      return {
        boundaryPathCount: 0,
        beaconRingCount: 0,
        beaconMotionEnabled: false,
        beaconRepeatPeriods: [],
        dataCountryCapColor: dataCountryVisualState?.capColor || "",
        dataCountryStrokeColor: dataCountryVisualState?.strokeColor || "",
        pathDashAnimateTime: 0,
      };
    };
    return () => {
      delete window.__isite2SelectCountry;
      delete window.__isite2GlobeControlState;
      delete window.__isite2SetPointOfView;
      delete window.__isite2GlobeAssetState;
      delete window.__isite2CountryVisualState;
    };
  }, [
    applyGlobeMode,
    countryNamesWithData,
    countryPolygons,
    hoveredCountry,
    selectedCountry,
  ]);

  const handleCountrySelect = useCallback((country: string) => {
    applyGlobeMode(country ? "focused" : "overview");
    setSelectedCountry(country);
    setSelectedCityKey("");
    setSelectedPropertyId("");
    setPanelSceneFilter("");
    setOverviewRegionFilter("");
    setExpandedClusterKey("");
    setIsCountryScopeOpen(false);
    setCountryQuery("");
  }, [applyGlobeMode]);

  const handlePropertySearchSelect = useCallback((result: PropertySearchResult) => {
    pendingPropertySelectionRef.current = result.property_id;
    applyGlobeMode("focused");
    setSceneFilter("");
    setEvidenceFilter("");
    setActionFilter("");
    setReviewOnly(false);
    setPanelSceneFilter("");
    setOverviewRegionFilter("");
    setSelectedCityKey("");
    setExpandedClusterKey("");
    setSelectedCountry(result.country);
    setSelectedPropertyId(result.property_id);
    setIsCountryScopeOpen(false);
    setCountryQuery("");
  }, [applyGlobeMode]);

  const handleCitySelect = useCallback((marker: CityMarker) => {
    applyGlobeMode("focused");
    setSelectedCountry(marker.country);
    setSelectedCityKey(marker.key);
    setSelectedPropertyId("");
    setPanelSceneFilter("");
    setOverviewRegionFilter("");
    setExpandedClusterKey("");
    setIsCountryScopeOpen(false);
    setCountryQuery("");
  }, [applyGlobeMode]);

  const handleGlobeMarkerSelect = useCallback((marker: DisplayGlobeMarker) => {
    if (marker.kind === "country") {
      handleCountrySelect(marker.country);
      return;
    }
    if (marker.kind === "city_cluster") {
      applyGlobeMode("focused");
      setExpandedClusterKey(marker.key);
      return;
    }
    handleCitySelect(marker);
  }, [applyGlobeMode, handleCitySelect, handleCountrySelect]);

  const handlePropertySelect = useCallback((propertyId: string) => {
    applyGlobeMode("focused");
    setOverviewRegionFilter("");
    setSelectedPropertyId(propertyId);
  }, [applyGlobeMode]);

  const handleSatelliteMarkerSelect = useCallback((marker: SatelliteMarker) => {
    applyGlobeMode("focused");
    if (marker.kind === "city_cluster" || marker.kind === "property_cluster") {
      return;
    }
    if (marker.kind === "city") {
      const city = cityMarkers.find((item) => item.key === marker.id);
      if (city) {
        handleCitySelect(city);
      }
      return;
    }
    handlePropertySelect(marker.id);
  }, [applyGlobeMode, cityMarkers, handleCitySelect, handlePropertySelect]);

  const handleBackToList = useCallback(() => {
    applyGlobeMode(selectedCountry ? "focused" : "overview");
    setSelectedPropertyId("");
  }, [applyGlobeMode, selectedCountry]);

  const handleClearCity = useCallback(() => {
    applyGlobeMode(selectedCountry ? "focused" : "overview");
    setSelectedCityKey("");
    setSelectedPropertyId("");
    setPanelSceneFilter("");
    setExpandedClusterKey("");
    setIsCountryScopeOpen(false);
    setCountryQuery("");
  }, [applyGlobeMode, selectedCountry]);

  const handleRetryCountryLoad = useCallback(() => {
    countryPayloadCacheRef.current.clear();
    setCountryLoadFailed(false);
    setCountryReloadToken((current) => current + 1);
  }, []);

  const handlePanelSceneSelect = useCallback((scene: string) => {
    setPanelSceneFilter((current) => current === scene ? "" : scene);
  }, []);

  const handleOverviewRegionSelect = useCallback((region: string) => {
    pointOfViewOverrideRef.current = null;
    setExpandedClusterKey("");
    setOverviewRegionFilter((current) => current === region ? "" : region);
  }, []);

  const handleOverviewRegionClear = useCallback(() => {
    pointOfViewOverrideRef.current = null;
    setExpandedClusterKey("");
    setOverviewRegionFilter("");
  }, []);

  const handleCountryExcelDownload = useCallback(async (country: string) => {
    if (!country || !enabledFeatures.exports || countryExportLoading) {
      return;
    }
    setCountryExportLoading(true);
    try {
      const params = new URLSearchParams({ country, locale });
      const response = await fetch(`/outputs/excel/country?${params.toString()}`);
      if (!response.ok) {
        let detail = uiLabel(
          localization,
          "ui.country_report_preparing",
          "The latest country report is being prepared.",
        );
        try {
          const payload = await response.json();
          if (typeof payload?.detail === "string" && response.status !== 409) {
            detail = payload.detail;
          }
        } catch {
          // Keep the localized fallback for non-JSON errors.
        }
        throw new Error(detail);
      }
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = responseFilename(
        response.headers.get("content-disposition"),
        `isite_${country.replaceAll(" ", "_")}_standard_report_${locale}.xlsx`,
      );
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(objectUrl);
      notify(
        uiLabel(localization, "ui.country_report_downloaded", "Country insight downloaded"),
        "success",
      );
    } catch (error) {
      notify(errorMessage(error), "error");
    } finally {
      setCountryExportLoading(false);
    }
  }, [countryExportLoading, enabledFeatures.exports, locale, localization, notify]);

  const handleGlobePointerMove = useCallback((event: React.PointerEvent<HTMLElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    setGlobePointerPosition({
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
    });
  }, []);

  const handleGlobePointerLeave = useCallback(() => {
    setGlobePointerPosition(null);
    setGlobeHoveredMarker(null);
    setHoveredCountry("");
  }, []);

  const handleGlobeMarkerHover = useCallback((marker: DisplayGlobeMarker | null) => {
    setGlobeHoveredMarker(marker ? globeMarkerHoverInfo(marker) : null);
    setHoveredCountry(marker?.kind === "country" ? marker.country : "");
  }, []);

  const openAuthModal = useCallback(() => {
    setLoginError("");
    setAuthModalOpen(true);
  }, []);

  const handleAppClickCapture = useCallback((event: React.MouseEvent<HTMLElement>) => {
    if (!authGateEnabled || authenticated || !isGuestClickTarget(event.target)) {
      return;
    }
    const currentCount = guestClickCountRef.current;
    if (currentCount >= guestClickLimit) {
      event.preventDefault();
      event.stopPropagation();
      openAuthModal();
      return;
    }
    const nextCount = currentCount + 1;
    guestClickCountRef.current = nextCount;
    setGuestClickCount(nextCount);
    writeStoredGuestClickCount(nextCount);
  }, [authGateEnabled, authenticated, guestClickLimit, openAuthModal]);

  const handleLoginSubmit = useCallback(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (loginLoading) {
      return;
    }
    setLoginLoading(true);
    setLoginError("");
    try {
      const session = await fetchJson<AuthSession>("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: loginUsername.trim(),
          password: loginPassword,
        }),
      });
      setAuthSession(session);
      guestClickCountRef.current = 0;
      setGuestClickCount(0);
      clearStoredGuestClickCount();
      setLoginPassword("");
      setAuthModalOpen(false);
      if (pendingServiceRequestOpenRef.current) {
        pendingServiceRequestOpenRef.current = false;
        setServiceRequestOpen(true);
      }
    } catch {
      setLoginError(uiLabel(localization, "ui.invalid_credentials", "Invalid username or password"));
    } finally {
      setLoginLoading(false);
    }
  }, [localization, loginLoading, loginPassword, loginUsername]);

  const handleOpenServiceRequest = useCallback(() => {
    if (!authenticated) {
      pendingServiceRequestOpenRef.current = true;
      openAuthModal();
      return;
    }
    setServiceRequestOpen(true);
  }, [authenticated, openAuthModal]);

  const handleOpenUpdates = useCallback(() => {
    setUpdatesOpen(true);
    const latest = updates.reduce(
      (maximum, update) => Math.max(maximum, new Date(update.published_at).getTime()),
      Date.now(),
    );
    setUpdatesLastSeen(latest);
    writeStoredUpdatesLastSeen(latest);
  }, [updates]);

  const handleLogout = useCallback(async () => {
    try {
      const session = await fetchJson<AuthSession>("/auth/logout", { method: "POST" });
      setAuthSession(session);
      guestClickCountRef.current = 0;
      setGuestClickCount(0);
      clearStoredGuestClickCount();
    } catch (error) {
      notify(errorMessage(error), "error");
    }
  }, [notify]);

  return (
	    <main
	      className="app-shell"
	      data-view-mode={viewMode}
	      data-localization-loading={showLocalizationThinking ? "true" : "false"}
	      onClickCapture={handleAppClickCapture}
	    >
      <section
        className="globe-stage"
        ref={stageRef}
        aria-label={geoVisualMode === "globe"
          ? `3D ${uiLabel(localization, "ui.opportunity_globe", "opportunity globe")}`
          : uiLabel(localization, "ui.satellite_map", "Satellite opportunity map")}
        data-geo-visual-mode={geoVisualMode}
        data-globe-mode={globeMode}
        data-auto-rotate={globeMode === "overview" ? "true" : "false"}
      >
        <header className="topbar">
          <div className="brand-lockup">
            <span className="eyebrow">iSite2</span>
            <h1>{uiLabel(localization, "ui.opportunity_globe", "Opportunity Globe")}</h1>
          </div>
          <PropertySearchControl
            localization={localization}
            onSelect={handlePropertySearchSelect}
          />
          <div className="toolbar" aria-label={uiLabel(localization, "ui.opportunity_actions", "Opportunity actions")}>
            <div
              className="language-toggle"
              aria-label={uiLabel(localization, "ui.language", "Language")}
              data-testid="language-toggle"
            >
              <Languages className="language-toggle-icon" size={16} aria-hidden="true" />
              {(runtimeConfig.localization?.supportedLocales?.length
                ? runtimeConfig.localization.supportedLocales
                : ["en", "zh"]
              ).map((item) => (
                <button
                  key={item}
                  className={locale === item ? "active" : ""}
                  type="button"
	                  aria-pressed={locale === item}
	                  title={item === "zh" ? "中文" : item.toUpperCase()}
	                  onClick={() => handleLocaleSelect(item)}
	                >
                  {item === "zh" ? "中文" : item.toUpperCase()}
                </button>
              ))}
            </div>
            {authGateEnabled && (
              <AuthStatusControl
                authenticated={authenticated}
                username={authSession.username || authConfig.usernameHint}
                clicksRemaining={guestClicksRemaining}
                localization={localization}
                onLogin={openAuthModal}
                onLogout={handleLogout}
              />
            )}
            {serviceRequestsEnabled && (
              <button
                type="button"
                className="service-request-button"
                onClick={handleOpenServiceRequest}
                aria-label={uiLabel(localization, "ui.request_title", "Submit a request")}
                data-testid="service-request-button"
              >
                <ClipboardPlus size={16} aria-hidden="true" />
                <span>{uiLabel(localization, "ui.request_short", "Request")}</span>
              </button>
            )}
            {serviceRequestsEnabled && (
              <button
                type="button"
                className="updates-button"
                onClick={handleOpenUpdates}
                aria-label={uiLabel(localization, "ui.updates_title", "Product updates")}
                data-guest-click-exempt="true"
                data-testid="updates-button"
              >
                <Bell size={16} aria-hidden="true" />
                <span>{uiLabel(localization, "ui.updates_short", "Updates")}</span>
                {updateUnreadCount > 0 && <strong className="updates-badge">{Math.min(updateUnreadCount, 99)}</strong>}
              </button>
            )}
            {enabledFeatures.rag && (
              <button
                aria-label={uiLabel(localization, "ui.ask_isite2", "Ask iSite2")}
                type="button"
                onClick={() => setIsRagOpen(true)}
              >
                <Bot size={16} aria-hidden="true" />
                <span>{uiLabel(localization, "ui.ask_isite2", "Ask iSite2")}</span>
              </button>
            )}
          </div>
        </header>

        {geoVisualMode === "globe" ? (
          <div
            className="geo-visual-layer geo-visual-layer-active map-interaction-hotzone"
            key="globe"
            onPointerMove={handleGlobePointerMove}
            onPointerLeave={handleGlobePointerLeave}
          >
            {useMockGlobe ? (
              <>
                <canvas
                  className="globe-test-canvas"
                  width={stageSize.width}
                  height={stageSize.height}
                  aria-label="Mock 3D opportunity globe"
                />
                {useMockCluster ? (
                  <div className="marker-overlay-layer mock-cluster-layer" aria-label="Mock globe marker layer">
                    {mockScreenGlobeMarkers.map((marker) => (
                      <GlobeMarkerButton
                        key={marker.key}
                        marker={marker}
                        active={marker.kind === "city" && marker.key === selectedCityKey}
                        hovered={marker.kind === "country" && marker.country === hoveredCountry}
                        onSelect={handleGlobeMarkerSelect}
                        onHover={handleGlobeMarkerHover}
                        spiderChild={marker.spiderChild}
                        style={{
                          opacity: marker.visible ? 1 : 0,
                          visibility: marker.visible ? "visible" : "hidden",
                          pointerEvents: marker.visible ? "auto" : "none",
                          transform: `translate3d(${marker.x}px, ${marker.y}px, 0) translate(-50%, -50%)`,
                        }}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="mock-marker-layer" aria-label="Mock globe marker layer">
                    {visibleGlobeMarkers.map((marker) => (
                      <GlobeMarkerButton
                        key={marker.key}
                        marker={marker}
                        active={marker.kind === "city" && marker.key === selectedCityKey}
                        hovered={marker.kind === "country" && marker.country === hoveredCountry}
                        onSelect={handleGlobeMarkerSelect}
                        onHover={handleGlobeMarkerHover}
                      />
                    ))}
                  </div>
                )}
              </>
            ) : (
              <>
                <Globe
                  ref={globeRef}
                  width={stageSize.width}
                  height={stageSize.height}
                  backgroundColor="rgba(0, 0, 0, 0)"
                  globeImageUrl={EARTH_IMAGE}
                  bumpImageUrl={EARTH_BUMP}
                  showAtmosphere
                  atmosphereColor="#55f1d1"
                  atmosphereAltitude={0.18}
                  polygonsData={countryPolygons}
                  polygonCapColor={(country) =>
                    countryPolygonVisualState(
                      country as CountryFeature,
                      selectedCountry,
                      hoveredCountry,
                      countryNamesWithData,
                    ).capColor}
                  polygonSideColor={(country) =>
                    countryPolygonVisualState(
                      country as CountryFeature,
                      selectedCountry,
                      hoveredCountry,
                      countryNamesWithData,
                    ).sideColor}
                  polygonStrokeColor={(country) =>
                    countryPolygonVisualState(
                      country as CountryFeature,
                      selectedCountry,
                      hoveredCountry,
                      countryNamesWithData,
                    ).strokeColor}
                  polygonAltitude={(country) =>
                    countryPolygonVisualState(
                      country as CountryFeature,
                      selectedCountry,
                      hoveredCountry,
                      countryNamesWithData,
                    ).altitude}
                  onPolygonClick={(country) =>
                    handleCountrySelect(dataCountryNameForPolygon(countryName(country as CountryFeature), countryNamesWithData))}
                  onPolygonHover={(country) =>
                    setHoveredCountry(dataCountryNameForPolygon(countryName(country as CountryFeature | null), countryNamesWithData))}
                  pointsData={[]}
                  labelsData={[]}
                  onGlobeReady={() => {
                    setGlobeReady(true);
                    if (overviewRegionFilter && overviewRegionFocus) {
                      configureGlobeControls(globeRef.current, "focused");
                      globeRef.current?.pointOfView({ ...overviewRegionFocus, altitude: OVERVIEW_ALTITUDE }, 0);
                      return;
                    }
                    const override = pointOfViewOverrideRef.current;
                    if (override) {
                      configureGlobeControls(globeRef.current, "focused");
                      globeRef.current?.pointOfView({ ...override, altitude: OVERVIEW_ALTITUDE }, 0);
                      return;
                    }
                    configureGlobeControls(globeRef.current, "overview");
                    globeRef.current?.pointOfView({ lat: 12, lng: 16, altitude: OVERVIEW_ALTITUDE }, 0);
                  }}
                />
                <div className="marker-overlay-layer" aria-label="Globe marker layer">
                  {screenGlobeMarkers.map((marker) => (
                    <GlobeMarkerButton
                      key={marker.key}
                      marker={marker}
                      active={marker.kind === "city" && marker.key === selectedCityKey}
                      hovered={marker.kind === "country" && marker.country === hoveredCountry}
                      onSelect={handleGlobeMarkerSelect}
                      onHover={handleGlobeMarkerHover}
                      spiderChild={marker.spiderChild}
                      style={{
                        opacity: marker.visible ? 1 : 0,
                        visibility: marker.visible ? "visible" : "hidden",
                        pointerEvents: marker.visible ? "auto" : "none",
                        transform: `translate3d(${marker.x}px, ${marker.y}px, 0) translate(-50%, -50%)`,
                      }}
                    />
                  ))}
                </div>
              </>
            )}
            <MapInteractionChrome
              pointerPosition={globePointerPosition}
              hoveredMarker={globeHoveredMarker}
              stageSize={stageSize}
            />
          </div>
        ) : (
          <React.Suspense fallback={<SatelliteNavigatorFallback mode={geoVisualMode} />}>
            <SatelliteNavigator
              mode={geoVisualMode}
              camera={satelliteCamera}
              markers={satelliteMarkers}
              stageSize={stageSize}
              mock={useMockSatellite}
              tileTemplate={runtimeConfig.map.satelliteTileTemplate}
              tileSize={runtimeConfig.map.satelliteTileSize}
              attribution={runtimeConfig.map.satelliteAttribution}
              propertyId={selectedPacket?.entity.property_id || ""}
              overlayMode={propertyOverlayMode}
              overlayTemplate={runtimeConfig.map.propertyOverlayTemplate || ""}
              overlayRadiusM={5000}
              footfallProviderConfigured={Boolean(runtimeConfig.map.footfallProvider?.configured)}
              onOverlayModeChange={setPropertyOverlayMode}
              onMarkerSelect={handleSatelliteMarkerSelect}
            />
          </React.Suspense>
        )}

	        {activeThinking && (
	          <AIThinkingOverlay
	            mode={activeThinking.mode}
	            stage={activeThinking.stage}
	            detail={activeThinking.detail}
	          />
	        )}

        <CountryScopeControl
          summaries={summaries}
          cityMarkers={cityMarkers}
          selectedCountry={selectedCountry}
          selectedCityKey={selectedCityKey}
          loading={loading}
          locale={locale}
          localization={localization}
          isOpen={isCountryScopeOpen}
          query={countryQuery}
          onToggle={() => setIsCountryScopeOpen((current) => !current)}
          onQueryChange={setCountryQuery}
          onSelectCountry={handleCountrySelect}
          onSelectCity={handleCitySelect}
          onClearCity={handleClearCity}
        />

        <div className="status-hud" aria-live="polite">
          <span>
            {loading
              ? uiLabel(localization, "ui.loading", "Loading")
              : selectedCountry
                ? `${formatNumber(visibleGlobeMarkers.length, locale)} ${uiLabel(localization, "ui.cities_visible", "cities visible")}`
                : `${formatNumber(visibleGlobeMarkers.length, locale)} ${uiLabel(localization, "ui.countries_visible", "countries visible")}`}
          </span>
          {selectedCountry && <strong>{selectedCountry}</strong>}
          {selectedCityMarker && <strong>{selectedCityMarker.city}</strong>}
          {hoveredCountry && <span>{hoveredCountry}</span>}
          {discoveryStatus && (
            <span>
              {uiLabel(localization, "ui.discovery_tasks", "Discovery {task_count} tasks / {evidence_count} new evidence", {
                task_count: taskBacklogTotal(discoveryStatus.task_backlog),
                evidence_count: discoveryStatus.pending_evidence_count,
              })}
            </span>
          )}
        </div>
      </section>

      <aside
        className={`insight-panel ${viewMode === "overview" ? "" : "workspace-panel"}`}
        aria-label="Country opportunity panel"
      >
        <CountryPanel
          viewMode={viewMode}
          selectedCountry={selectedCountry}
          selectedCityMarker={selectedCityMarker}
          selectedSummary={panelSummary}
          regionSummaries={regionSummaries}
          countryCount={overviewCountryCount}
          countryCityCount={selectedCityMarker ? 1 : selectedCountryCityCount}
          packets={panelPackets}
          packetsLoading={packetsLoading}
          countryLoadFailed={countryLoadFailed}
          selectedPropertyId={selectedPropertyId}
          overviewRegionFilter={overviewRegionFilter}
          panelSceneFilter={panelSceneFilter}
          contextLabel={workspaceContext}
          gapSummary={gapSummary}
          listMode={listMode}
          detailTab={detailTab}
          onListModeChange={setListMode}
          onSelectProperty={handlePropertySelect}
          onBackToList={handleBackToList}
          onClearCity={handleClearCity}
          onRetryCountryLoad={handleRetryCountryLoad}
          onPanelSceneSelect={handlePanelSceneSelect}
          onOverviewRegionSelect={handleOverviewRegionSelect}
          onOverviewRegionClear={handleOverviewRegionClear}
          onSetDetailTab={setDetailTab}
          locale={locale}
          localization={localization}
          canExportCountry={enabledFeatures.exports}
          countryExportLoading={countryExportLoading}
          onExportCountry={handleCountryExcelDownload}
        />
      </aside>

      {isRagOpen && enabledFeatures.rag && (
        <RagDrawer
          selectedCountry={selectedCountry}
          selectedPacket={selectedPacket}
          question={ragQuestion}
          result={ragResult}
          loading={ragLoading}
          error={ragError}
          onQuestionChange={setRagQuestion}
          onAsk={askRag}
          onClose={() => setIsRagOpen(false)}
          localization={localization}
        />
      )}

      {authGateEnabled && authModalOpen && (
        <AuthModal
          username={loginUsername}
          password={loginPassword}
          loading={loginLoading}
          error={loginError}
          guestClickLimit={guestClickLimit}
          localization={localization}
          onUsernameChange={setLoginUsername}
          onPasswordChange={setLoginPassword}
          onSubmit={handleLoginSubmit}
          onClose={() => {
            pendingServiceRequestOpenRef.current = false;
            setAuthModalOpen(false);
          }}
        />
      )}

      <ServiceRequestDrawer
        open={serviceRequestOpen}
        locale={locale}
        localization={localization}
        countries={summaries.map((summary) => summary.country)}
        initialCountry={selectedCountry}
        onClose={() => setServiceRequestOpen(false)}
      />

      <UpdatesDrawer
        open={updatesOpen}
        updates={updates}
        loading={updatesLoading}
        error={updatesError}
        localization={localization}
        onClose={() => setUpdatesOpen(false)}
      />

      {toast && <div className={`toast ${toast.tone}`}>{toast.message}</div>}
    </main>
  );
}

function AuthStatusControl({
  authenticated,
  username,
  clicksRemaining,
  localization,
  onLogin,
  onLogout,
}: {
  authenticated: boolean;
  username: string;
  clicksRemaining: number;
  localization: LocalizationPayload;
  onLogin: () => void;
  onLogout: () => void;
}) {
  if (authenticated) {
    return (
      <div className="auth-status signed-in" data-guest-click-exempt="true">
        <UserRound size={15} aria-hidden="true" />
        <span>{uiLabel(localization, "ui.signed_in_as", "Signed in as {username}", { username })}</span>
        <button
          type="button"
          aria-label={uiLabel(localization, "ui.sign_out", "Sign out")}
          title={uiLabel(localization, "ui.sign_out", "Sign out")}
          onClick={onLogout}
        >
          <LogOut size={15} aria-hidden="true" />
        </button>
      </div>
    );
  }
  return (
    <button
      className="auth-status guest"
      type="button"
      data-guest-click-exempt="true"
      onClick={onLogin}
      aria-label={uiLabel(localization, "ui.sign_in", "Sign in")}
    >
      <LogIn size={15} aria-hidden="true" />
      <span>{uiLabel(localization, "ui.guest_access", "Guest access")}</span>
      <strong>
        {uiLabel(localization, "ui.guest_clicks_remaining", "{count} clicks left", {
          count: clicksRemaining,
        })}
      </strong>
    </button>
  );
}

function AuthModal({
  username,
  password,
  loading,
  error,
  guestClickLimit,
  localization,
  onUsernameChange,
  onPasswordChange,
  onSubmit,
  onClose,
}: {
  username: string;
  password: string;
  loading: boolean;
  error: string;
  guestClickLimit: number;
  localization: LocalizationPayload;
  onUsernameChange: (value: string) => void;
  onPasswordChange: (value: string) => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
  onClose: () => void;
}) {
  return (
    <div className="auth-modal-backdrop" data-auth-modal="true">
      <section
        className="auth-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="auth-modal-title"
      >
        <button
          className="auth-modal-close"
          type="button"
          aria-label={uiLabel(localization, "ui.close", "Close")}
          onClick={onClose}
        >
          <X size={17} aria-hidden="true" />
        </button>
        <div className="auth-modal-heading">
          <LogIn size={20} aria-hidden="true" />
          <h2 id="auth-modal-title">{uiLabel(localization, "ui.login_required", "Login required")}</h2>
          <p>
            {uiLabel(localization, "ui.login_required_body", "Guest access includes {limit} clicks. Sign in to continue.", {
              limit: guestClickLimit,
            })}
          </p>
        </div>
        <form className="auth-form" onSubmit={onSubmit}>
          <label>
            <span>{uiLabel(localization, "ui.username", "Username")}</span>
            <input
              value={username}
              onChange={(event) => onUsernameChange(event.target.value)}
              autoComplete="username"
              disabled={loading}
            />
          </label>
          <label>
            <span>{uiLabel(localization, "ui.password", "Password")}</span>
            <input
              value={password}
              onChange={(event) => onPasswordChange(event.target.value)}
              type="password"
              autoComplete="current-password"
              disabled={loading}
            />
          </label>
          {error && <div className="auth-error" role="alert">{error}</div>}
          <button className="auth-submit" type="submit" disabled={loading}>
            <LogIn size={16} aria-hidden="true" />
            <span>{uiLabel(localization, "ui.sign_in", "Sign in")}</span>
          </button>
        </form>
      </section>
    </div>
  );
}

function PropertySearchControl({
  localization,
  onSelect,
}: {
  localization: LocalizationPayload;
  onSelect: (result: PropertySearchResult) => void;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<PropertySearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const trimmedQuery = query.trim();

  useEffect(() => {
    if (!trimmedQuery) {
      setResults([]);
      setLoading(false);
      setOpen(false);
      setActiveIndex(-1);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      const cacheKey = trimmedQuery.normalize("NFKC").toLocaleLowerCase();
      const cached = propertySearchCache.get(cacheKey);
      if (cached) {
        setResults(cached.results || []);
        setOpen(true);
        setActiveIndex(cached.results?.length ? 0 : -1);
        setLoading(false);
        return;
      }
      setLoading(true);
      let request = propertySearchRequests.get(cacheKey);
      if (!request) {
        request = fetchJson<PropertySearchResponse>(
          `/properties/search?q=${encodeURIComponent(trimmedQuery)}&limit=20`,
        );
        propertySearchRequests.set(cacheKey, request);
        void request.then(
          () => propertySearchRequests.delete(cacheKey),
          () => propertySearchRequests.delete(cacheKey),
        );
      }
      void request.then((payload) => {
        propertySearchCache.set(cacheKey, payload);
        if (cancelled) {
          return;
        }
        setResults(payload.results || []);
        setOpen(true);
        setActiveIndex(payload.results?.length ? 0 : -1);
      }).catch(() => {
        if (cancelled) {
          return;
        }
        setResults([]);
        setOpen(true);
        setActiveIndex(-1);
      }).finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [trimmedQuery]);

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, []);

  const choose = (result: PropertySearchResult) => {
    setQuery("");
    setOpen(false);
    setActiveIndex(-1);
    onSelect(result);
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      setOpen(false);
      return;
    }
    if (event.key === "ArrowDown" && results.length) {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((current) => (current + 1 + results.length) % results.length);
      return;
    }
    if (event.key === "ArrowUp" && results.length) {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((current) => (current - 1 + results.length) % results.length);
      return;
    }
    if (event.key === "Enter" && open && activeIndex >= 0 && results[activeIndex]) {
      event.preventDefault();
      choose(results[activeIndex]);
    }
  };

  return (
    <div className="property-search-control" ref={rootRef}>
      <Search size={16} aria-hidden="true" />
      <input
        type="search"
        value={query}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls="property-search-results"
        aria-activedescendant={activeIndex >= 0 ? `property-search-result-${activeIndex}` : undefined}
        aria-label={uiLabel(localization, "ui.search_properties", "Search properties")}
        placeholder={uiLabel(localization, "ui.search_properties", "Search properties")}
        onChange={(event) => setQuery(event.target.value)}
        onFocus={() => trimmedQuery && setOpen(true)}
        onKeyDown={handleKeyDown}
      />
      {query && (
        <button
          className="property-search-clear"
          type="button"
          title={uiLabel(localization, "ui.clear_search", "Clear search")}
          aria-label={uiLabel(localization, "ui.clear_search", "Clear search")}
          onClick={() => setQuery("")}
        >
          <X size={14} aria-hidden="true" />
        </button>
      )}
      {open && (
        <div className="property-search-results" id="property-search-results" role="listbox">
          {loading && (
            <div className="property-search-state">
              {uiLabel(localization, "ui.searching_properties", "Searching properties")}
            </div>
          )}
          {!loading && results.map((result, index) => (
            <button
              id={`property-search-result-${index}`}
              key={result.property_id}
              className={index === activeIndex ? "active" : ""}
              type="button"
              role="option"
              aria-selected={index === activeIndex}
              onMouseEnter={() => setActiveIndex(index)}
              onClick={() => choose(result)}
            >
              <strong>{result.property_name}</strong>
              {result.matched_name !== result.property_name && (
                <small>{result.matched_name}</small>
              )}
              <span>
                {result.city} · {result.country} · {sceneLabel(result.scene_type, localization)}
              </span>
            </button>
          ))}
          {!loading && results.length === 0 && (
            <div className="property-search-state">
              {uiLabel(localization, "ui.no_properties_found", "No properties found")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function CountryScopeControl({
  summaries,
  cityMarkers,
  selectedCountry,
  selectedCityKey,
  loading,
  locale,
  localization,
  isOpen,
  query,
  onToggle,
  onQueryChange,
  onSelectCountry,
  onSelectCity,
  onClearCity,
}: {
  summaries: CountrySummary[];
  cityMarkers: CityMarker[];
  selectedCountry: string;
  selectedCityKey: string;
  loading: boolean;
  locale: string;
  localization: LocalizationPayload;
  isOpen: boolean;
  query: string;
  onToggle: () => void;
  onQueryChange: (value: string) => void;
  onSelectCountry: (country: string) => void;
  onSelectCity: (city: CityMarker) => void;
  onClearCity: () => void;
}) {
  const sortedSummaries = useMemo(() => {
    return [...summaries].sort((a, b) => {
      if (b.map_point_count !== a.map_point_count) {
        return b.map_point_count - a.map_point_count;
      }
      return a.country.localeCompare(b.country);
    });
  }, [summaries]);
  const normalizedQuery = query.trim().toLowerCase();
  const filteredSummaries = normalizedQuery
    ? sortedSummaries.filter((summary) => summary.country.toLowerCase().includes(normalizedQuery))
    : sortedSummaries;
  const sortedCities = useMemo(
    () => cityMarkers
      .filter((marker) => marker.country === selectedCountry)
      .sort((a, b) => b.candidateCount - a.candidateCount || a.city.localeCompare(b.city)),
    [cityMarkers, selectedCountry],
  );
  const filteredCities = normalizedQuery
    ? sortedCities.filter((marker) => marker.city.toLowerCase().includes(normalizedQuery))
    : sortedCities;
  const selectedCity = sortedCities.find((marker) => marker.key === selectedCityKey);
  const selectedSummary = summaries.find((summary) => summary.country === selectedCountry);
  const totalCandidateCount = summaries.reduce((total, summary) => total + summary.candidate_count, 0);
  const summaryLabel = selectedCountry
    ? `${selectedCity?.city || uiLabel(localization, "ui.all_cities_in_country", "All cities in {country}", { country: selectedCountry })} · ${
        loading
          ? uiLabel(localization, "ui.loading", "Loading")
          : `${formatNumber(selectedCity?.candidateCount ?? selectedSummary?.candidate_count ?? 0, locale)} ${uiLabel(localization, "ui.candidates", "candidates").toLowerCase()}`
      }`
    : `${uiLabel(localization, "ui.all_countries", "All countries")} · ${formatNumber(summaries.length, locale)} ${uiLabel(localization, "ui.countries", "countries").toLowerCase()} · ${
        loading
          ? uiLabel(localization, "ui.loading", "Loading")
          : `${formatNumber(totalCandidateCount, locale)} ${uiLabel(localization, "ui.candidates", "candidates").toLowerCase()}`
      }`;

  return (
    <section
      className={`country-scope ${selectedCountry ? "city-context" : "country-context"} ${isOpen ? "open" : ""}`}
      aria-label={uiLabel(localization, selectedCountry ? "ui.city_scope" : "ui.country_scope", selectedCountry ? "City scope" : "Country scope")}
    >
      <button
        className="country-scope-trigger"
        type="button"
        aria-expanded={isOpen}
        aria-controls="country-scope-menu"
        aria-label={`${uiLabel(localization, selectedCountry ? "ui.city_scope" : "ui.country_scope", selectedCountry ? "City scope" : "Country scope")}: ${summaryLabel}`}
        onClick={onToggle}
      >
        <MapPin size={16} aria-hidden="true" />
        <span>{summaryLabel}</span>
        <ChevronDown size={16} aria-hidden="true" />
      </button>
      {isOpen && (
        <div className="country-scope-menu" id="country-scope-menu">
          <label className="country-search">
            <Search size={14} aria-hidden="true" />
            <input
              aria-label={uiLabel(localization, selectedCountry ? "ui.search_cities" : "ui.search_countries", selectedCountry ? "City search" : "Country search")}
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
              placeholder={uiLabel(localization, selectedCountry ? "ui.search_cities" : "ui.search_countries", selectedCountry ? "Search cities" : "Search countries")}
              autoFocus
            />
          </label>
          {selectedCountry && (
            <button className="country-row scope-back" type="button" onClick={() => onSelectCountry("")}>
              <span>
                {uiLabel(localization, "ui.back_to_countries", "Back to countries")}
                <small>{uiLabel(localization, "ui.country_scope", "Country scope")}</small>
              </span>
              <ArrowLeft size={16} aria-hidden="true" />
            </button>
          )}
          <button
            className={`country-row all ${selectedCountry ? "" : "active"}`}
            type="button"
            onClick={selectedCountry ? onClearCity : () => onSelectCountry("")}
          >
            <span>
              {selectedCountry
                ? uiLabel(localization, "ui.all_cities_in_country", "All cities in {country}", { country: selectedCountry })
                : uiLabel(localization, "ui.all_countries", "All countries")}
              <small>
                {selectedCountry
                  ? `${formatNumber(sortedCities.length, locale)} ${uiLabel(localization, "ui.cities", "cities").toLowerCase()}`
                  : `${formatNumber(summaries.length, locale)} ${uiLabel(localization, "ui.countries", "countries").toLowerCase()}`}
              </small>
            </span>
            <strong>{formatNumber(selectedCountry ? selectedSummary?.candidate_count || 0 : totalCandidateCount, locale)}</strong>
          </button>
          <div className="country-result-list">
            {selectedCountry ? filteredCities.map((marker) => (
              <button
                key={marker.key}
                className={`country-row ${marker.key === selectedCityKey ? "active" : ""}`}
                type="button"
                onClick={() => onSelectCity(marker)}
              >
                <span>
                  {marker.city}
                  <small>
                    {formatNumber(marker.candidateCount, locale)} {uiLabel(localization, "ui.candidates", "candidates").toLowerCase()} · {formatNumber(marker.localityCount || 0, locale)} {uiLabel(localization, "ui.localities", "localities").toLowerCase()}
                  </small>
                </span>
                <strong>{formatNumber(marker.candidateCount, locale)}</strong>
              </button>
            )) : filteredSummaries.map((summary) => (
              <button
                key={summary.country}
                className={`country-row ${summary.country === selectedCountry ? "active" : ""}`}
                type="button"
                onClick={() => onSelectCountry(summary.country)}
              >
                <span className="country-row-copy">
                  <span className="country-row-title">
                    {summary.country}
                    <ScanMaturityBadge maturity={summary.scan_maturity} localization={localization} compact />
                  </span>
                  <small>
                    {formatNumber(summary.candidate_count, locale)} {uiLabel(localization, "ui.candidates", "candidates").toLowerCase()} ·{" "}
                    {formatNumber(summary.review_count, locale)} {uiLabel(localization, "ui.review", "review").toLowerCase()}
                  </small>
                </span>
                <strong>{formatNumber(summary.map_point_count, locale)}</strong>
              </button>
            ))}
            {(selectedCountry ? filteredCities.length : filteredSummaries.length) === 0 && (
              <div className="country-empty">{uiLabel(localization, selectedCountry ? "ui.no_cities_found" : "ui.no_countries_found", selectedCountry ? "No cities found" : "No countries found")}</div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function RagDrawer({
  selectedCountry,
  selectedPacket,
  question,
  result,
  loading,
  error,
  onQuestionChange,
  onAsk,
  onClose,
  localization,
}: {
  selectedCountry: string;
  selectedPacket: SitePacket | null;
  question: string;
  result: RagQueryResponse | null;
  loading: boolean;
  error: string;
  onQuestionChange: (value: string) => void;
  onAsk: () => void;
  onClose: () => void;
  localization: LocalizationPayload;
}) {
  const contextCountry = selectedPacket?.entity.country || selectedCountry || uiLabel(localization, "ui.all_countries", "All countries");
  const contextProperty = selectedPacket?.entity.property_name || "No property selected";
  const examplePrompts = selectedPacket
    ? [
        "Why is this property recommended?",
        "Which evidence supports this action class?",
        "What should the review queue verify next?",
      ]
    : [
        "Which properties need review first?",
        "Summarize evidence gaps for this scope.",
        "Show high-value opportunities by scene.",
      ];

  return (
    <>
      <button className="rag-scrim" type="button" aria-label="Close Ask iSite2" onClick={onClose} />
      <aside className="rag-drawer" role="dialog" aria-modal="true" aria-label="Ask iSite2 RAG assistant">
        <div className="rag-head">
          <div>
            <span className="eyebrow">{uiLabel(localization, "ui.rag_entry", "RAG Entry")}</span>
            <h2>{uiLabel(localization, "ui.ask_isite2", "Ask iSite2")}</h2>
          </div>
          <button type="button" aria-label="Close Ask iSite2" onClick={onClose}>
            <X size={17} aria-hidden="true" />
          </button>
        </div>

        <div className="rag-context" aria-label={uiLabel(localization, "ui.current_rag_context", "Current RAG context")}>
          <div>
            <span>{uiLabel(localization, "ui.countries", "Country")}</span>
            <strong>{contextCountry}</strong>
          </div>
          <div>
            <span>{uiLabel(localization, "ui.property", "Property")}</span>
            <strong>{contextProperty}</strong>
          </div>
        </div>

        <div className="rag-examples" aria-label="Example RAG questions">
          {examplePrompts.map((prompt) => (
            <button key={prompt} type="button" onClick={() => onQuestionChange(prompt)}>
              {prompt}
            </button>
          ))}
        </div>

        <label className="rag-input">
          <span>{uiLabel(localization, "ui.question", "Question")}</span>
          <textarea
            value={question}
            onChange={(event) => onQuestionChange(event.target.value)}
            placeholder="Ask about evidence, review queue, value class, or recommended action..."
          />
        </label>

        {error && (
          <div className="rag-disconnected error" role="alert">
            <Bot size={18} aria-hidden="true" />
            <div>
              <strong>{uiLabel(localization, "ui.ask_failed", "Ask iSite2 failed")}</strong>
              <span>{error}</span>
            </div>
          </div>
        )}

        {result ? (
          <RagResult result={result} />
        ) : (
          <div className="rag-disconnected" role="status">
            <Bot size={18} aria-hidden="true" />
            <div>
              <strong>{uiLabel(localization, "ui.retrieval_ready", "Evidence-backed retrieval ready")}</strong>
              <span>{uiLabel(localization, "ui.retrieval_ready_body", "Answers will include citations and concrete review actions.")}</span>
            </div>
          </div>
        )}

        <button
          className="rag-send"
          type="button"
          disabled={!question.trim() || loading}
          onClick={onAsk}
        >
          <Bot size={16} aria-hidden="true" />
          <span>{loading ? uiLabel(localization, "ui.retrieving", "Retrieving") : uiLabel(localization, "ui.send", "Send")}</span>
        </button>
      </aside>
    </>
  );
}

function RagResult({ result }: { result: RagQueryResponse }) {
  return (
    <div className="rag-result" aria-label="Ask iSite2 response">
      <section>
        <span>Answer</span>
        <p>{result.answer}</p>
      </section>
      {result.priority_recommendations.length > 0 && (
        <section>
          <span>Priority</span>
          <div className="rag-priority-list">
            {result.priority_recommendations.map((item) => (
              <article key={`${item.property_id || item.property_name}-${item.priority_band}`}>
                <strong>{item.property_name}</strong>
                <small>{item.priority_band}</small>
                <p>{item.rationale}</p>
              </article>
            ))}
          </div>
        </section>
      )}
      {result.review_actions.length > 0 && (
        <section>
          <span>Review Actions</span>
          <ul>
            {result.review_actions.slice(0, 4).map((action) => (
              <li key={action}>{action}</li>
            ))}
          </ul>
        </section>
      )}
      <section>
        <span>Citations</span>
        <div className="rag-citation-list">
          {result.citations.map((citation) => (
            <a
              key={citation.chunk_id}
              href={citation.source_url || "#"}
              target="_blank"
              rel="noreferrer"
            >
              <strong>{citation.source_name || citation.source_url || "Indexed evidence"}</strong>
              <small>
                {citation.source_tier || "Unknown tier"}
                {citation.source_date ? ` · ${citation.source_date}` : ""}
              </small>
              <p>{citation.excerpt}</p>
            </a>
          ))}
        </div>
      </section>
    </div>
  );
}

function AIThinkingOverlay({
  stage,
  detail = "Preparing the opportunity globe",
  mode = "bootstrap",
}: {
  stage: string;
  detail?: string;
  mode?: "bootstrap" | "localization";
}) {
  return (
    <div className="ai-thinking-overlay" data-thinking-mode={mode} role="status" aria-live="polite">
      <div className="ai-thinking-card">
        <span className="ai-thinking-orbit" aria-hidden="true" />
        <div>
          <span className="eyebrow">AI Thinking</span>
          <strong>{stage}</strong>
          <small>{detail}</small>
        </div>
        <span className="ai-thinking-scanline" aria-hidden="true" />
      </div>
    </div>
  );
}

function SatelliteNavigatorFallback({ mode }: { mode: GeoVisualMode }) {
  return (
    <div
      className="geo-visual-layer satellite-navigator satellite-navigator-loading"
      aria-label="Satellite opportunity map"
      data-satellite-mode={mode}
      data-tile-status="tiles-loading"
    >
      <div className="satellite-mock-canvas" aria-hidden="true" />
      <div className="satellite-shade" aria-hidden="true" />
      <div className="satellite-status">
        <span>{satelliteModeLabel(mode)}</span>
        <strong>Loading map engine</strong>
      </div>
    </div>
  );
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

function aiThinkingStageLabel({
  globeReady,
  overviewMarkersReady,
  overviewRegionReady,
  overviewSummaryReady,
}: {
  globeReady: boolean;
  overviewMarkersReady: boolean;
  overviewRegionReady: boolean;
  overviewSummaryReady: boolean;
}): string {
  if (!globeReady) {
    return "Initializing globe engine";
  }
  if (!overviewSummaryReady) {
    return "Loading country intelligence";
  }
  if (!overviewMarkersReady) {
    return "Projecting opportunity markers";
  }
  if (!overviewRegionReady) {
    return "Preparing regional brief";
  }
  return "Preparing regional brief";
}

function CountryPanel({
  viewMode,
  selectedCountry,
  selectedCityMarker,
  selectedSummary,
  regionSummaries,
  countryCount,
  countryCityCount,
  packets,
  packetsLoading,
  countryLoadFailed,
  selectedPropertyId,
  overviewRegionFilter,
  panelSceneFilter,
  contextLabel,
  gapSummary,
  listMode,
  detailTab,
  onListModeChange,
  onSelectProperty,
  onBackToList,
  onClearCity,
  onRetryCountryLoad,
  onPanelSceneSelect,
  onOverviewRegionSelect,
  onOverviewRegionClear,
  onSetDetailTab,
  locale,
  localization,
  canExportCountry,
  countryExportLoading,
  onExportCountry,
}: {
  viewMode: ViewMode;
  selectedCountry: string;
  selectedCityMarker: CityMarker | null;
  selectedSummary: CountrySummary;
  regionSummaries: RegionSummary[];
  countryCount: number;
  countryCityCount: number;
  packets: SitePacket[];
  packetsLoading: boolean;
  countryLoadFailed: boolean;
  selectedPropertyId: string;
  overviewRegionFilter: string;
  panelSceneFilter: string;
  contextLabel: string;
  gapSummary: EvidenceGapSummary;
  listMode: WorkspaceListMode;
  detailTab: "evidence" | "inference" | "review";
  onListModeChange: (mode: WorkspaceListMode) => void;
  onSelectProperty: (id: string) => void;
  onBackToList: () => void;
  onClearCity: () => void;
  onRetryCountryLoad: () => void;
  onPanelSceneSelect: (scene: string) => void;
  onOverviewRegionSelect: (region: string) => void;
  onOverviewRegionClear: () => void;
  onSetDetailTab: (tab: "evidence" | "inference" | "review") => void;
  locale: string;
  localization: LocalizationPayload;
  canExportCountry: boolean;
  countryExportLoading: boolean;
  onExportCountry: (country: string) => void;
}) {
  const [cardRenderState, setCardRenderState] = useState({
    key: "",
    limit: CARD_RENDER_BATCH_SIZE,
  });
  const selectedPacket =
    packets.find((packet) => packet.entity.property_id === selectedPropertyId) ?? null;
  const title = selectedCityMarker?.city || selectedCountry || uiLabel(localization, "ui.all_candidate_countries", "All candidate countries");
  const isOverview = !selectedCountry && !selectedCityMarker;
  const cardModels = useMemo(
    () => packets.map((packet) => propertyCardViewModel(packet, localization)),
    [localization, packets],
  );
  const cardScopeKey = [
    listMode,
    locale,
    localization.locale,
    selectedCountry,
    selectedCityMarker?.key || "",
    panelSceneFilter,
    cardModels.length,
    cardModels[0]?.propertyId || "",
    cardModels[cardModels.length - 1]?.propertyId || "",
  ].join("|");
  const renderedCardLimit = cardRenderState.key === cardScopeKey
    ? cardRenderState.limit
    : CARD_RENDER_BATCH_SIZE;
  const visibleCardModels = useMemo(
    () => cardModels.slice(0, renderedCardLimit),
    [cardModels, renderedCardLimit],
  );
  const kpis = isOverview
    ? [
        { label: uiLabel(localization, "ui.countries", "Countries"), value: countryCount },
        { label: uiLabel(localization, "ui.candidates", "Candidates"), value: selectedSummary.candidate_count },
        { label: uiLabel(localization, "ui.sources", "Evidence"), value: selectedSummary.source_count },
      ]
    : [
        { label: uiLabel(localization, "ui.cities", "Cities"), value: countryCityCount },
        { label: uiLabel(localization, "ui.candidates", "Candidates"), value: selectedSummary.candidate_count },
        { label: uiLabel(localization, "ui.sources", "Evidence"), value: selectedSummary.source_count },
      ];

  useEffect(() => {
    if (isOverview || listMode !== "card" || renderedCardLimit >= cardModels.length) {
      return;
    }
    let cancelled = false;
    const idleWindow = window as IdleSchedulerWindow;
    const appendBatch = () => {
      if (cancelled) {
        return;
      }
      setCardRenderState((current) => {
        const currentLimit = current.key === cardScopeKey
          ? current.limit
          : CARD_RENDER_BATCH_SIZE;
        return {
          key: cardScopeKey,
          limit: Math.min(currentLimit + CARD_RENDER_BATCH_SIZE, cardModels.length),
        };
      });
    };
    const cleanup = typeof idleWindow.requestIdleCallback === "function"
      && typeof idleWindow.cancelIdleCallback === "function"
      ? (() => {
          const handle = idleWindow.requestIdleCallback?.(appendBatch, { timeout: 240 }) ?? 0;
          return () => idleWindow.cancelIdleCallback?.(handle);
        })()
      : (() => {
          const handle = window.setTimeout(appendBatch, CARD_RENDER_BATCH_DELAY_MS);
          return () => window.clearTimeout(handle);
        })();
    return () => {
      cancelled = true;
      cleanup();
    };
  }, [cardModels.length, cardScopeKey, isOverview, listMode, renderedCardLimit]);

  if (selectedPacket) {
    const entity = selectedPacket.entity;
    return (
      <div className="panel-scroll property-focus-panel" data-panel-mode={viewMode}>
        <WorkspaceContextBar
          title={entity.property_name}
          subtitle={contextLabel}
          kpis={kpis}
          gapSummary={gapSummary}
          listMode={listMode}
          onListModeChange={onListModeChange}
          showListToggle={false}
          locale={locale}
          localization={localization}
          scanMaturity={selectedCityMarker ? undefined : selectedSummary.scan_maturity}
          exportCountry={canExportCountry ? selectedCountry : ""}
          exportLoading={countryExportLoading}
          onExportCountry={onExportCountry}
        />
        <button className="back-button" type="button" onClick={onBackToList}>
          <ArrowLeft size={16} aria-hidden="true" />
          <span>{uiLabel(localization, "ui.back_to_list", "Back to list")}</span>
        </button>
        <div className="property-focus-head">
          <span className="eyebrow">{uiLabel(localization, "ui.property_dossier", "Property Dossier")}</span>
          <h2>{entity.property_name}</h2>
          <div className="focus-meta">
            <span>{entity.country}</span>
            <span>{entity.city}</span>
            {entity.city_assignment?.locality
              && entity.city_assignment.locality !== entity.city && (
                <span>
                  {uiLabel(localization, "ui.locality", "Locality / Commune")}: {entity.city_assignment.locality}
                </span>
              )}
            <span>{selectedPacket.localized?.entity?.scene_label || sceneLabel(entity.scene_type, localization)}</span>
          </div>
        </div>
        <DetailDossier
          packet={selectedPacket}
          detailTab={detailTab}
          onSetDetailTab={onSetDetailTab}
          locale={locale}
          localization={localization}
        />
      </div>
    );
  }

  return (
    <div className="panel-scroll" data-panel-mode={viewMode}>
      {!isOverview && (
        <WorkspaceContextBar
          title={title}
          subtitle={contextLabel}
          kpis={kpis}
          gapSummary={gapSummary}
          listMode={listMode}
          onListModeChange={onListModeChange}
          locale={locale}
          localization={localization}
          scanMaturity={selectedCityMarker ? undefined : selectedSummary.scan_maturity}
          exportCountry={canExportCountry ? selectedCountry : ""}
          exportLoading={countryExportLoading}
          onExportCountry={onExportCountry}
        />
      )}
      <div className="panel-head">
        <span className="eyebrow">{uiLabel(localization, "ui.country_opportunities", "Country Opportunities")}</span>
        <h2>{title}</h2>
        {selectedCityMarker && (
          <button className="city-clear-button" type="button" onClick={onClearCity}>
            <ArrowLeft size={15} aria-hidden="true" />
            <span>{uiLabel(localization, "ui.all_cities_in_country", "All cities in {country}", { country: selectedCityMarker.country })}</span>
          </button>
        )}
        <div className="kpi-grid">
          {kpis.map((item) => (
            <Kpi key={item.label} label={item.label} value={item.value} />
          ))}
        </div>
      </div>

      <SceneDistribution
        scenes={selectedSummary.scenes}
        activeScene={panelSceneFilter}
        interactive={!isOverview}
        onSelectScene={onPanelSceneSelect}
        localization={localization}
      />

      {isOverview && (
        <RegionDistribution
          regions={regionSummaries}
          activeRegion={overviewRegionFilter}
          locale={locale}
          localization={localization}
          onSelectRegion={onOverviewRegionSelect}
          onClearRegion={onOverviewRegionClear}
        />
      )}

      {!isOverview && listMode === "card" && (
        <div className="property-list">
          {visibleCardModels.map((card) => (
            <PropertyCard
              key={card.propertyId}
              card={card}
              active={card.propertyId === selectedPropertyId}
              onSelectProperty={onSelectProperty}
            />
          ))}
        </div>
      )}

      {!isOverview && listMode === "dense" && (
        <DensePropertyList
          packets={packets}
          activePropertyId={selectedPropertyId}
          onSelectProperty={onSelectProperty}
          localization={localization}
        />
      )}

      {!isOverview && packets.length === 0 && packetsLoading && (
        <div className="empty-panel">
          <MapPin size={20} aria-hidden="true" />
          <strong>{uiLabel(localization, "ui.ai_thinking", "AI Thinking")}</strong>
          <span>{uiLabel(localization, "ui.evidence_packet_loading", "Map markers are ready while the full evidence packet list loads.")}</span>
        </div>
      )}

      {packets.length === 0 && !packetsLoading && !isOverview && countryLoadFailed && (
        <div className="empty-panel" role="alert">
          <MapPin size={20} aria-hidden="true" />
          <strong>{uiLabel(localization, "ui.property_load_failed", "Property data could not be loaded.")}</strong>
          <span>{uiLabel(localization, "ui.property_load_failed_body", "This country has candidates. Retry without losing your current selection.")}</span>
          <button className="empty-panel-action" type="button" onClick={onRetryCountryLoad}>
            <RefreshCw size={15} aria-hidden="true" />
            <span>{uiLabel(localization, "ui.retry", "Retry")}</span>
          </button>
        </div>
      )}

      {packets.length === 0 && !packetsLoading && !isOverview && !countryLoadFailed && (
        <div className="empty-panel">
          <MapPin size={20} aria-hidden="true" />
          <strong>{selectedCountry
            ? uiLabel(localization, "ui.no_visible_points", "No visible points")
            : uiLabel(localization, "ui.no_scan_data", "No scan data")}</strong>
          <span>
            {selectedCountry
              ? uiLabel(localization, "ui.adjust_filters", "Adjust filters or return to all countries.")
              : uiLabel(localization, "ui.no_candidates_in_db", "No candidate properties are currently stored in the database.")}
          </span>
        </div>
      )}
    </div>
  );
}

function WorkspaceContextBar({
  title,
  subtitle,
  kpis,
  gapSummary,
  listMode,
  onListModeChange,
  showListToggle = true,
  locale,
  localization,
  exportCountry = "",
  exportLoading = false,
  onExportCountry,
  scanMaturity,
}: {
  title: string;
  subtitle: string;
  kpis: Array<{ label: string; value: number }>;
  gapSummary: EvidenceGapSummary;
  listMode: WorkspaceListMode;
  onListModeChange: (mode: WorkspaceListMode) => void;
  showListToggle?: boolean;
  locale: string;
  localization: LocalizationPayload;
  exportCountry?: string;
  exportLoading?: boolean;
  onExportCountry?: (country: string) => void;
  scanMaturity?: ScanMaturity;
}) {
  return (
    <section className="workspace-context-bar" aria-label={uiLabel(localization, "ui.workspace_context", "Workspace context")}>
      <div className="workspace-context-title">
        <span className="eyebrow">{uiLabel(localization, "ui.workspace", "Workspace")}</span>
        <div className="workspace-context-heading">
          <strong>{title}</strong>
          <ScanMaturityBadge maturity={scanMaturity} localization={localization} />
        </div>
        <small>{subtitle}</small>
      </div>
      <div className="workspace-context-kpis">
        {kpis.map((item) => (
          <span key={item.label}>
            <small>{item.label}</small>
            <strong>{formatNumber(item.value, locale)}</strong>
          </span>
        ))}
        <span>
          <small>{uiLabel(localization, "ui.review", "Review")}</small>
          <strong>{formatNumber(gapSummary.reviewQueueCount, locale)}</strong>
        </span>
      </div>
      {exportCountry && onExportCountry && (
        <button
          className="country-excel-download"
          type="button"
          disabled={exportLoading}
          title={uiLabel(localization, "ui.export_country_insight", "Export country insight")}
          onClick={() => onExportCountry(exportCountry)}
        >
          <FileSpreadsheet size={16} aria-hidden="true" />
          <span>
            {exportLoading
              ? uiLabel(localization, "ui.preparing_country_report", "Preparing report")
              : uiLabel(localization, "ui.export_country_insight", "Export country insight")}
          </span>
        </button>
      )}
      {showListToggle && (
        <div className="list-mode-toggle" aria-label="Property list display mode">
          <button
            className={listMode === "card" ? "active" : ""}
            type="button"
            aria-pressed={listMode === "card"}
            aria-label={uiLabel(localization, "ui.card_view", "Card view")}
            onClick={() => onListModeChange("card")}
          >
            <LayoutGrid size={15} aria-hidden="true" />
            <span>{uiLabel(localization, "ui.card", "Card")}</span>
          </button>
          <button
            className={listMode === "dense" ? "active" : ""}
            type="button"
            aria-pressed={listMode === "dense"}
            aria-label={uiLabel(localization, "ui.dense_view", "Dense view")}
            onClick={() => onListModeChange("dense")}
          >
            <List size={15} aria-hidden="true" />
            <span>{uiLabel(localization, "ui.dense", "Dense")}</span>
          </button>
        </div>
      )}
    </section>
  );
}

function ScanMaturityBadge({
  maturity,
  localization,
  compact = false,
}: {
  maturity?: ScanMaturity;
  localization: LocalizationPayload;
  compact?: boolean;
}) {
  if (!maturity) {
    return null;
  }
  const labels: Record<ScanMaturityLevel, string> = {
    seed_scan: uiLabel(localization, "ui.scan_maturity_seed", "Seed scan"),
    deep_expansion: uiLabel(localization, "ui.scan_maturity_expanding", "Deep expansion"),
    near_saturation: uiLabel(localization, "ui.scan_maturity_saturated", "Near saturation"),
  };
  const descriptions: Record<ScanMaturityLevel, string> = {
    seed_scan: uiLabel(localization, "ui.scan_maturity_seed_help", "Initial candidate pool; sources and scenes still need expansion."),
    deep_expansion: uiLabel(localization, "ui.scan_maturity_expanding_help", "Multi-source or multi-round expansion completed; the pool is not yet considered saturated."),
    near_saturation: uiLabel(localization, "ui.scan_maturity_saturated_help", "The planned source matrix has produced no new candidates across consecutive rounds."),
  };
  const label = labels[maturity.level];
  const description = descriptions[maturity.level];
  return (
    <span
      className={`scan-maturity-badge ${maturity.level} ${compact ? "compact" : ""}`}
      title={description}
      aria-label={`${uiLabel(localization, "ui.scan_maturity", "Scan maturity")}: ${label}. ${description}`}
      data-scan-maturity={maturity.level}
    >
      <span aria-hidden="true" />
      {label}
    </span>
  );
}

function Kpi({ label, value }: { label: string; value: number }) {
  return (
    <div className="kpi">
      <span>{label}</span>
      <strong>{formatNumber(value)}</strong>
    </div>
  );
}

function SceneDistribution({
  scenes,
  activeScene = "",
  interactive = false,
  onSelectScene,
  localization,
}: {
  scenes: Record<string, number>;
  activeScene?: string;
  interactive?: boolean;
  onSelectScene?: (scene: string) => void;
  localization?: LocalizationPayload;
}) {
  const entries = Object.entries(scenes);
  const total = entries.reduce((sum, [, value]) => sum + value, 0) || 1;
  return (
    <section className="scene-band" aria-label="Scene distribution">
      {entries.map(([scene, count]) => {
        const isActive = activeScene === scene;
        const content = (
          <>
            <span>{sceneLabel(scene, localization)}</span>
            <div className="bar-track">
              <div style={{ width: `${Math.max(10, (count / total) * 100)}%` }} />
            </div>
            <strong>{count}</strong>
          </>
        );
        if (interactive && onSelectScene) {
          return (
            <button
              key={scene}
              className={`scene-row ${isActive ? "active" : ""}`}
              type="button"
              aria-pressed={isActive}
              onClick={() => onSelectScene(scene)}
            >
              {content}
            </button>
          );
        }
        return (
          <div key={scene} className="scene-row">
            {content}
          </div>
        );
      })}
    </section>
  );
}

function RegionDistribution({
  regions,
  activeRegion,
  locale,
  localization,
  onSelectRegion,
  onClearRegion,
}: {
  regions: RegionSummary[];
  activeRegion: string;
  locale: string;
  localization: LocalizationPayload;
  onSelectRegion: (region: string) => void;
  onClearRegion: () => void;
}) {
  if (regions.length === 0) {
    return null;
  }
  return (
    <section className="region-band" aria-label="Region distribution">
      <div className="region-band-head">
        <span>{uiLabel(localization, "ui.region_distribution", "Region distribution")}</span>
        <div className="region-band-actions">
          {activeRegion && (
            <button className="region-back-button" type="button" onClick={onClearRegion}>
              {uiLabel(localization, "ui.back_to_all", "Back to all")}
            </button>
          )}
          <strong>{formatNumber(regions.reduce((total, region) => total + region.country_count, 0), locale)} {uiLabel(localization, "ui.countries", "countries").toLowerCase()}</strong>
        </div>
      </div>
      {regions.map((region) => {
        const displayLabel = regionDisplayLabel(region.region);
        return (
          <button
            className={`region-row ${activeRegion === region.region ? "active" : ""}`}
            data-region={region.region}
            key={region.region}
            title={displayLabel === region.region ? undefined : region.region}
            type="button"
            onClick={() => onSelectRegion(region.region)}
          >
            <div>
              <strong>{displayLabel}</strong>
              <span>
                {formatNumber(region.country_count, locale)} {uiLabel(localization, "ui.countries", "countries").toLowerCase()} · {formatNumber(region.source_count, locale)} {uiLabel(localization, "ui.sources", "evidence").toLowerCase()}
              </span>
            </div>
            <div className="region-metrics">
              <span>
                <strong>{formatNumber(region.candidate_count, locale)}</strong>
                {uiLabel(localization, "ui.candidates", "Candidates")}
              </span>
              <span>
                <strong>{formatNumber(region.source_count, locale)}</strong>
                {uiLabel(localization, "ui.sources", "Evidence")}
              </span>
            </div>
          </button>
        );
      })}
    </section>
  );
}

function DensePropertyList({
  packets,
  activePropertyId,
  onSelectProperty,
  localization,
}: {
  packets: SitePacket[];
  activePropertyId: string;
  onSelectProperty: (id: string) => void;
  localization: LocalizationPayload;
}) {
  return (
    <div className="dense-property-list" aria-label="Dense property list">
      <div className="dense-property-head" aria-hidden="true">
        <span>{uiLabel(localization, "ui.property", "Property")}</span>
        <span>{uiLabel(localization, "ui.scene", "Scene")}</span>
        <span>{uiLabel(localization, "ui.primary_metric", "Primary metric")}</span>
        <span>{uiLabel(localization, "ui.network_experience", "Network Experience")}</span>
        <span>{uiLabel(localization, "ui.evidence", "Evidence")}</span>
        <span>{uiLabel(localization, "ui.sources", "Evidence")}</span>
        <span>{uiLabel(localization, "ui.review", "Review")}</span>
        <span>{uiLabel(localization, "ui.action", "Action")}</span>
      </div>
      {packets.map((packet) => {
        const entity = packet.entity;
        const metric = localizedPrimaryMetricText(packet, localization);
        const networkExperience = networkExperienceCardSummary(packet, localization);
        const sourceCount = evidenceSourceCount(packet);
        return (
          <button
            key={entity.property_id}
            className={`dense-property-row ${entity.property_id === activePropertyId ? "active" : ""}`}
            type="button"
            aria-pressed={entity.property_id === activePropertyId}
            onClick={() => onSelectProperty(entity.property_id)}
          >
            <span>
              <strong>{entity.property_name}</strong>
              <small>{entity.city}</small>
            </span>
            <span>{packet.localized?.entity?.scene_label || sceneLabel(entity.scene_type, localization)}</span>
            <span>{metric}</span>
            <span>{networkExperience || uiLabel(localization, "fallback.unknown", "Unknown")}</span>
            <span>{packet.localized?.conclusion?.evidence_status_label || enumLabel(packet.conclusion.evidence_status, localization)}</span>
            <span>{formatNumber(sourceCount, localization.locale)}</span>
            <span>{formatNumber(packet.review_queue.length, localization.locale)}</span>
            <span>{packet.localized?.conclusion?.action_class_label || enumLabel(packet.conclusion.action_class, localization)}</span>
          </button>
        );
      })}
    </div>
  );
}

const PropertyCard = React.memo(function PropertyCard({
  card,
  active,
  onSelectProperty,
}: {
  card: PropertyCardViewModel;
  active: boolean;
  onSelectProperty: (id: string) => void;
}) {
  const handleClick = useCallback(() => {
    onSelectProperty(card.propertyId);
  }, [card.propertyId, onSelectProperty]);
  return (
    <article className={`property-card ${active ? "active" : ""}`} data-action-tone={card.actionTone}>
      <button type="button" onClick={handleClick} aria-pressed={active}>
        <HeroMedia image={card.image} propertyName={card.propertyName} />
        <div className="card-body">
          <div className="card-title-row">
            <div>
              <h3>{card.propertyName}</h3>
              <span className="card-scene">{card.sceneLabel}</span>
            </div>
            <strong className="card-evidence">{formatCompactEvidenceCount(card.sourceCount, card.sourceLabel)}</strong>
          </div>
          <p className="card-metric">{card.metric}</p>
          {card.networkExperience && (
            <p className="card-network">{card.networkExperience}</p>
          )}
          <div className="card-meta">
            <span className="card-city">{card.city}</span>
            <span className="card-value">{card.valueClass}</span>
          </div>
          <div className="card-foot">
            <strong className="card-action">{card.actionClass}</strong>
          </div>
        </div>
      </button>
      {card.image && (
        <a className="image-source" href={card.image.source_url} target="_blank" rel="noreferrer">
          <ExternalLink size={13} aria-hidden="true" />
          <span>{card.image.source_name}</span>
        </a>
      )}
    </article>
  );
});

function propertyCardViewModel(packet: SitePacket, localization?: LocalizationPayload): PropertyCardViewModel {
  const entity = packet.entity;
  return {
    propertyId: entity.property_id,
    propertyName: entity.property_name,
    city: entity.city,
    sceneLabel: packet.localized?.entity?.scene_label || sceneLabel(entity.scene_type, localization),
    metric: localizedPrimaryMetricText(packet, localization),
    networkExperience: networkExperienceCardSummary(packet, localization),
    valueClass: packet.localized?.conclusion?.value_class_label
      || enumLabel(packet.conclusion.value_class, localization),
    actionClass: packet.localized?.conclusion?.action_class_label
      || enumLabel(packet.conclusion.action_class, localization),
    actionTone: propertyCardTone(packet.conclusion.action_class),
    sourceCount: evidenceSourceCount(packet),
    sourceLabel: uiLabel(localization, "ui.sources", "evidence").toLowerCase(),
    image: entity.hero_image,
  };
}

function propertyCardTone(actionClass: string): PropertyCardViewModel["actionTone"] {
  const normalized = normalizeToken(actionClass);
  if (
    normalized.includes("survey")
    || normalized.includes("direct")
    || normalized.includes("priority")
    || normalized.includes("recommend")
    || normalized.includes("first")
  ) {
    return "primary";
  }
  if (
    normalized.includes("review")
    || normalized.includes("verify")
    || normalized.includes("low")
    || normalized.includes("insufficient")
  ) {
    return "review";
  }
  if (
    normalized.includes("monitor")
    || normalized.includes("watch")
    || normalized.includes("hold")
  ) {
    return "watch";
  }
  return "neutral";
}

function visualToneForStatus(value: string | number): "primary" | "review" | "watch" | "neutral" {
  const normalized = normalizeToken(String(value));
  if (normalized.includes("supported") || normalized.includes("ready") || normalized.includes("verified")) {
    return "primary";
  }
  if (normalized.includes("review") || normalized.includes("unknown") || normalized.includes("required")) {
    return "review";
  }
  if (normalized.includes("monitor") || normalized.includes("pending")) {
    return "watch";
  }
  return "neutral";
}

function normalizeToken(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function formatCompactEvidenceCount(count: number, label: string): string {
  return `${formatNumber(count)} ${label}`;
}

function HeroMedia({ image, propertyName }: { image?: HeroImage | null; propertyName: string }) {
  const [imageMode, setImageMode] = useState<"proxy" | "direct" | "failed">("proxy");
  const [canLoadImage, setCanLoadImage] = useState(false);
  const placeholderRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    setImageMode("proxy");
    setCanLoadImage(false);
  }, [image?.url]);
  useEffect(() => {
    if (!image || imageMode === "failed" || canLoadImage) {
      return;
    }
    const element = placeholderRef.current;
    if (!element || !("IntersectionObserver" in window)) {
      setCanLoadImage(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting || entry.intersectionRatio > 0)) {
          setCanLoadImage(true);
          observer.disconnect();
        }
      },
      { rootMargin: CARD_IMAGE_ROOT_MARGIN },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [canLoadImage, image, imageMode]);
  if (!image || imageMode === "failed") {
    return (
      <div className="hero-fallback">
        <ImageOff size={22} aria-hidden="true" />
        <span>{propertyName}</span>
      </div>
    );
  }
  if (!canLoadImage) {
    return (
      <div
        ref={placeholderRef}
        className="hero-placeholder"
        aria-hidden="true"
      />
    );
  }
  return (
    <img
      src={imageMode === "proxy" ? heroImageSrc(image.url) : image.url}
      alt={image.alt_text || propertyName}
      loading="lazy"
      decoding="async"
      onError={() => setImageMode((mode) => (mode === "proxy" ? "direct" : "failed"))}
    />
  );
}

function DetailDossier({
  packet,
  detailTab,
  onSetDetailTab,
  locale,
  localization,
}: {
  packet: SitePacket;
  detailTab: "evidence" | "inference" | "review";
  onSetDetailTab: (tab: "evidence" | "inference" | "review") => void;
  locale: string;
  localization: LocalizationPayload;
}) {
  const entity = packet.entity;
  const primaryMetric = localizedPrimaryMetricText(packet, localization);
  const sourceCount = evidenceSourceCount(packet);
  const networkExperience = networkExperienceMetrics(packet, localization);
  const actionTone = propertyCardTone(packet.conclusion.action_class);
  const evidenceStatus = packet.localized?.conclusion?.evidence_status_label || enumLabel(packet.conclusion.evidence_status, localization);
  const coordinateStatus = entity.coordinate_status;
  return (
    <section className="dossier cockpit-dossier" data-action-tone={actionTone}>
      <div className="dossier-visual-cockpit">
        <DossierHeroMedia image={entity.hero_image} propertyName={entity.property_name} localization={localization} />
        <div className="dossier-hero">
          <div>
            <span className="eyebrow">{uiLabel(localization, "ui.investment_cockpit", "Investment cockpit")}</span>
            <h3>{entity.property_name}</h3>
            <div className="dossier-hero-meta">
              <span>{entity.city}</span>
              {entity.city_assignment?.locality
                && entity.city_assignment.locality !== entity.city && (
                  <span>
                    {uiLabel(localization, "ui.locality", "Locality / Commune")}: {entity.city_assignment.locality}
                  </span>
                )}
              <span>{packet.localized?.entity?.scene_label || sceneLabel(entity.scene_type, localization)}</span>
              <span>{entity.geocode_precision}</span>
            </div>
            <p>{localizedReasonToRecommend(packet, localization)}</p>
          </div>
          <a className="dossier-map-link" href={entity.google_maps_link || "#"} target="_blank" rel="noreferrer">
            <MapPin size={15} aria-hidden="true" />
            <span>{uiLabel(localization, "ui.google_maps", "Google Maps")}</span>
          </a>
        </div>
      </div>

      <div className="dossier-cockpit-grid">
        <article className="primary-metric-card">
          <span>{primaryMetricFilterLabel(entity.scene_type, localization)}</span>
          <strong>{primaryMetric}</strong>
          <small>{packet.localized?.entity?.scene_label || sceneLabel(entity.scene_type, localization)}</small>
        </article>
        <article className="decision-card action-card" data-tone={actionTone}>
          <span>{uiLabel(localization, "ui.action_class", "Action Class")}</span>
          <strong>{packet.localized?.conclusion?.action_class_label || enumLabel(packet.conclusion.action_class, localization)}</strong>
          <small>{packet.localized?.conclusion?.value_class_label || enumLabel(packet.conclusion.value_class, localization)} · {packet.localized?.conclusion?.recommended_solution_label || enumLabel(packet.conclusion.recommended_solution, localization)}</small>
        </article>
        <article className="decision-card next-action-card">
          <span>{uiLabel(localization, "ui.next_action", "Next Action")}</span>
          <strong>{localizedNextAction(packet, localization)}</strong>
        </article>
      </div>

      <div className="dossier-status-strip">
        <DossierStatusPill label={uiLabel(localization, "ui.evidence", "Evidence")} value={evidenceStatus} tone={visualToneForStatus(evidenceStatus)} />
        <DossierStatusPill label={uiLabel(localization, "ui.sources", "Evidence")} value={formatNumber(sourceCount, locale)} tone="neutral" />
        <DossierStatusPill label={uiLabel(localization, "ui.review", "Review")} value={formatNumber(packet.review_queue.length, locale)} tone={packet.review_queue.length > 0 ? "review" : "neutral"} />
        <DossierStatusPill label={uiLabel(localization, "ui.coordinate", "Coordinate")} value={coordinateStatus} tone={visualToneForStatus(coordinateStatus)} />
        <DossierStatusPill label={uiLabel(localization, "ui.indoor_rat", "Indoor RAT")} value={packet.localized?.build_status?.indoor_rat_label || enumLabel(packet.build_status.indoor_rat, localization)} />
      </div>

      <div className="metric-grid network-metric-grid">
        <Metric label={uiLabel(localization, "ui.annual_visits", "Annual Visits")} value={formatNumber(packet.scene.annual_visits_est, locale)} />
        <Metric label={uiLabel(localization, "ui.busy_users", "Busy Users")} value={formatNumber(packet.demand?.busy_hour_users, locale)} />
        <Metric label={uiLabel(localization, "ui.busy_traffic_gb", "Busy Traffic GB")} value={formatNumber(packet.demand?.busy_hour_traffic_gb, locale)} />
        <Metric label={uiLabel(localization, "ui.bandwidth_mbps", "Bandwidth Mbps")} value={formatNumber(packet.demand?.busy_hour_bandwidth_mbps, locale)} />
        <Metric label={uiLabel(localization, "ui.map_source", "Map Source")} value={entity.map_source || uiLabel(localization, "fallback.unknown", "Unknown")} />
      </div>

      <NetworkExperiencePanel
        experience={networkExperience}
        locale={locale}
        localization={localization}
      />

      <ComplaintSignalsPanel
        complaints={packet.network_signals?.complaints}
        locale={locale}
        localization={localization}
      />

      <div className="tabs">
        {(["evidence", "inference", "review"] as const).map((tab) => (
          <button
            key={tab}
            className={detailTab === tab ? "active" : ""}
            type="button"
            onClick={() => onSetDetailTab(tab)}
          >
            {tab === "evidence"
              ? uiLabel(localization, "ui.evidence", "Evidence")
              : tab === "inference"
                ? uiLabel(localization, "ui.inference", "Inference")
                : uiLabel(localization, "ui.review_tab", "Review")}
          </button>
        ))}
      </div>
      {detailTab === "evidence" && (
        <RecordList
          items={packet.evidence}
          render={(item) => (
            <>
              <strong>{localizedEvidenceItem(packet, item)?.field_group_label || item.field_group}</strong>
              <p>{localizedEvidenceItem(packet, item)?.field_value || item.field_value}</p>
              {localizedEvidenceItem(packet, item)?.field_value_original
                && localizedEvidenceItem(packet, item)?.field_value_original !== localizedEvidenceItem(packet, item)?.field_value && (
                <small>
                  {uiLabel(localization, "ui.original_evidence", "Original evidence")}: {localizedEvidenceItem(packet, item)?.field_value_original}
                </small>
              )}
              <a href={item.source_url} target="_blank" rel="noreferrer">
                {item.source_name} · {localizedEvidenceItem(packet, item)?.source_tier_label || item.source_tier}
              </a>
            </>
          )}
        />
      )}
      {detailTab === "inference" && (
        <RecordList
          items={packet.inference}
          render={(item) => (
            <>
              <strong>{item.inferred_field}</strong>
              <p>{localizedInferenceItem(packet, item)?.inference_chain || item.inference_chain}</p>
              <span>{item.inference_confidence}</span>
            </>
          )}
        />
      )}
      {detailTab === "review" && (
        <RecordList
          items={packet.review_queue}
          render={(item) => (
            <>
              <strong>{localizedReviewItem(packet, item)?.reason || item.reason}</strong>
              <p>{localizedReviewItem(packet, item)?.next_action || item.next_action}</p>
              <span>{localizedReviewItem(packet, item)?.status_label || item.status}</span>
            </>
          )}
        />
      )}
    </section>
  );
}

function NetworkExperiencePanel({
  experience,
  locale,
  localization,
}: {
  experience: NetworkExperienceSummary;
  locale: string;
  localization: LocalizationPayload;
}) {
  const metrics = [experience.mobile, experience.fixed].filter(Boolean) as NetworkExperienceMetric[];
  const defaultKind: NetworkExperienceKind = experience.mobile ? "mobile" : "fixed";
  const [activeKind, setActiveKind] = useState<NetworkExperienceKind>(defaultKind);
  const activeMetric = experience[activeKind] || metrics[0];
  const toggleMetrics = metrics.length > 1 ? metrics : [];

  useEffect(() => {
    if (!experience[activeKind]) {
      setActiveKind(defaultKind);
    }
  }, [activeKind, defaultKind, experience.fixed, experience.mobile]);

  if (metrics.length === 0) {
    return null;
  }
  return (
    <section className="network-experience-panel" aria-label={uiLabel(localization, "ui.network_experience", "Network Experience")}>
      <div className="network-experience-head">
        <div className="network-experience-title">
          <span className="eyebrow">{uiLabel(localization, "ui.network_experience", "Network Experience")}</span>
          <strong>{uiLabel(localization, "ui.ookla_tile_proxy", "Ookla tile proxy")}</strong>
        </div>
        {toggleMetrics.length > 0 && (
          <div className="network-experience-toggle" role="group" aria-label={uiLabel(localization, "ui.network_experience", "Network Experience")}>
            {toggleMetrics.map((metric) => (
              <button
                aria-pressed={activeKind === metric.kind}
                className={activeKind === metric.kind ? "active" : ""}
                key={metric.kind}
                onClick={() => setActiveKind(metric.kind)}
                type="button"
              >
                {metric.label}
              </button>
            ))}
          </div>
        )}
        <small>{uiLabel(localization, "ui.network_experience_proxy_note", "Tile-level experience proxy, not indoor DAS/build evidence.")}</small>
      </div>
      {activeMetric && (
        <div className="network-experience-cards">
          <article className="network-experience-card" key={activeMetric.kind}>
            <div className="network-experience-card-head">
              <div>
                <span>{activeMetric.label}</span>
                {activeMetric.sourceDate && <small>{activeMetric.sourceDate}</small>}
              </div>
              <span className={`network-confidence ${activeMetric.confidence}`}>
                {networkConfidenceLabel(activeMetric.confidence, localization)}
              </span>
            </div>
            <div className="network-experience-values">
              <Metric
                label={uiLabel(localization, "ui.download_mbps", "Download Mbps")}
                value={formatNullableNumber(activeMetric.downloadMbps, locale)}
              />
              <Metric
                label={uiLabel(localization, "ui.upload_mbps", "Upload Mbps")}
                value={formatNullableNumber(activeMetric.uploadMbps, locale)}
              />
              <Metric
                label={uiLabel(localization, "ui.latency_ms", "Latency ms")}
                value={formatNullableNumber(activeMetric.latencyMs, locale)}
              />
              <Metric
                label={uiLabel(localization, "ui.samples", "Samples")}
                value={networkSampleLabel(activeMetric, locale, localization)}
              />
            </div>
            <div className="network-experience-meta">
              <span>
                {uiLabel(localization, "ui.tile_distance", "Tile distance")}: {formatNullableNumber(activeMetric.distanceM, locale)} m
              </span>
              <a href={activeMetric.sourceUrl} target="_blank" rel="noreferrer">
                <ExternalLink size={13} aria-hidden="true" />
                <span>{activeMetric.sourceName}</span>
              </a>
            </div>
          </article>
        </div>
      )}
    </section>
  );
}

function ComplaintSignalsPanel({
  complaints,
  locale,
  localization,
}: {
  complaints?: ComplaintSignal | null;
  locale: string;
  localization: LocalizationPayload;
}) {
  const categoryRows = Object.entries(complaints?.category_counts || {})
    .filter(([, count]) => count > 0)
    .sort((left, right) => right[1] - left[1]);
  const hasQualifiedData = Boolean(complaints && complaints.valid_complaint_count > 0);
  const pressure = complaints?.pressure_level || "insufficient";
  return (
    <section
      className="complaint-signals-panel"
      data-state={hasQualifiedData ? "available" : "empty"}
      aria-label={uiLabel(localization, "ui.network_complaints", "Network complaints")}
    >
      <div className="complaint-signals-head">
        <div>
          <span className="eyebrow">{uiLabel(localization, "ui.network_complaints", "Network complaints")}</span>
          <strong>{uiLabel(localization, "ui.property_complaint_signals", "Property-level public signals")}</strong>
        </div>
        <small>{uiLabel(localization, "ui.complaint_signal_note", "Aggregated public network complaints only. This does not prove indoor DAS/build status.")}</small>
      </div>
      {!hasQualifiedData ? (
        <div className="complaint-signals-empty">
          <Search size={18} aria-hidden="true" />
          <span>{uiLabel(localization, "ui.complaint_empty", "No compliant property-level network complaints are currently available.")}</span>
        </div>
      ) : (
        <>
          <div className="complaint-signals-metrics">
            <Metric
              label={uiLabel(localization, "ui.valid_complaints", "Valid complaints")}
              value={formatNumber(complaints?.valid_complaint_count, locale)}
            />
            <Metric
              label={uiLabel(localization, "ui.complaint_sources", "Complaint sources")}
              value={formatNumber(complaints?.source_count, locale)}
            />
            <Metric
              label={uiLabel(localization, "ui.complaint_pressure", "Pressure")}
              value={complaintPressureLabel(pressure, localization)}
            />
            <Metric
              label={uiLabel(localization, "ui.complaint_period", "Observation window")}
              value={formatTemplate(
                uiLabel(localization, "ui.complaint_period_days", "{days} days"),
                { days: formatNumber(complaints?.period_days, locale) },
              )}
            />
          </div>
          <div className="complaint-signals-detail">
            <div>
              <span>{uiLabel(localization, "ui.complaint_categories", "Complaint categories")}</span>
              <strong>
                {categoryRows.length > 0
                  ? categoryRows.map(([category, count]) => (
                    `${complaintCategoryLabel(category, localization)} ${formatNumber(count, locale)}`
                  )).join(" · ")
                  : uiLabel(localization, "fallback.unknown", "Unknown")}
              </strong>
            </div>
            <div>
              <span>{uiLabel(localization, "ui.complaint_latest", "Latest observation")}</span>
              <strong>{formatComplaintDate(complaints?.latest_observed_at, locale, localization)}</strong>
            </div>
          </div>
        </>
      )}
    </section>
  );
}

function DossierHeroMedia({
  image,
  propertyName,
  localization,
}: {
  image?: HeroImage | null;
  propertyName: string;
  localization: LocalizationPayload;
}) {
  const [imageMode, setImageMode] = useState<"proxy" | "direct" | "failed">("proxy");
  useEffect(() => {
    setImageMode("proxy");
  }, [image?.url]);
  const hasVerifiedImage = Boolean(image && imageMode !== "failed");
  const imageMeta = image
    ? [image.source_date, image.license].filter(Boolean).join(" · ")
    : "";
  return (
    <figure className={`dossier-image-card ${hasVerifiedImage ? "has-image" : "missing-image"}`}>
      <div className="dossier-image-frame">
        {hasVerifiedImage && image ? (
          <img
            src={imageMode === "proxy" ? heroImageSrc(image.url) : image.url}
            alt={image.alt_text || propertyName}
            loading="eager"
            onError={() => setImageMode((mode) => (mode === "proxy" ? "direct" : "failed"))}
          />
        ) : (
          <div className="dossier-image-fallback">
            <ImageOff size={28} aria-hidden="true" />
            <strong>{uiLabel(localization, "fallback.no_verified_public_image", "No verified public image")}</strong>
            <span>{uiLabel(localization, "fallback.no_verified_public_image_body", "Real public image evidence is missing or unavailable.")}</span>
          </div>
        )}
      </div>
      <figcaption className="dossier-image-caption">
        {hasVerifiedImage && image ? (
          <>
            <a href={image.source_url} target="_blank" rel="noreferrer">
              <ExternalLink size={13} aria-hidden="true" />
              <span>{image.source_name}</span>
            </a>
            <span>{imageMeta || uiLabel(localization, "fallback.public_image_source", "Public image source")}</span>
          </>
        ) : (
          <span>{uiLabel(localization, "fallback.no_generated_images_notice", "Satellite maps, screenshots, and generated visuals are not used as real property images.")}</span>
        )}
      </figcaption>
    </figure>
  );
}

function DossierStatusPill({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string | number;
  tone?: "primary" | "review" | "watch" | "neutral";
}) {
  return (
    <span className="dossier-status-pill" data-tone={tone}>
      <small>{label}</small>
      <strong>{value}</strong>
    </span>
  );
}

function Metric({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value ?? "Unknown"}</strong>
    </div>
  );
}

function RecordList<T>({ items, render }: { items: T[]; render: (item: T) => React.ReactNode }) {
  if (items.length === 0) {
    return <div className="record-list empty">No rows</div>;
  }
  return <div className="record-list">{items.map((item, index) => <article key={index}>{render(item)}</article>)}</div>;
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

function readStoredGuestClickCount(): number {
  let rawValue = "0";
  try {
    rawValue = window.localStorage.getItem(GUEST_CLICK_STORAGE_KEY) || "0";
  } catch {
    rawValue = "0";
  }
  const value = Number(rawValue);
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

function writeStoredGuestClickCount(value: number): void {
  try {
    window.localStorage.setItem(GUEST_CLICK_STORAGE_KEY, String(Math.max(0, Math.floor(value))));
  } catch {
    // Guest gating still works for the current render cycle if localStorage is unavailable.
  }
}

function clearStoredGuestClickCount(): void {
  try {
    window.localStorage.removeItem(GUEST_CLICK_STORAGE_KEY);
  } catch {
    // Ignore storage policy failures.
  }
}

function readStoredUpdatesLastSeen(): number {
  try {
    const value = Number(window.localStorage.getItem(UPDATES_LAST_SEEN_STORAGE_KEY) || "0");
    return Number.isFinite(value) ? value : 0;
  } catch {
    return 0;
  }
}

function writeStoredUpdatesLastSeen(value: number): void {
  try {
    window.localStorage.setItem(UPDATES_LAST_SEEN_STORAGE_KEY, String(value));
  } catch {
    // Ignore storage policy failures.
  }
}

function authConfigFromRuntime(config: RuntimeConfig): NonNullable<RuntimeConfig["auth"]> {
  const fallback = DEFAULT_RUNTIME_CONFIG.auth!;
  return {
    enabled: Boolean(config.auth?.enabled ?? fallback.enabled),
    guestClickLimit:
      typeof config.auth?.guestClickLimit === "number" && config.auth.guestClickLimit > 0
        ? config.auth.guestClickLimit
        : fallback.guestClickLimit,
    usernameHint: config.auth?.usernameHint || fallback.usernameHint,
  };
}

function isGuestClickTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) {
    return false;
  }
  if (target.closest("[data-auth-modal='true'], [data-guest-click-exempt='true']")) {
    return false;
  }
  if (target.closest("button, a, input, select, textarea, [role='button']")) {
    return true;
  }
  return Boolean(target.closest(".map-interaction-hotzone, .globe-stage, .insight-panel"));
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function keepSelectedPropertyIdIfVisible(current: string, packets: SitePacket[]): string {
  if (!current) {
    return "";
  }
  return packets.some((packet) => packet.entity.property_id === current) ? current : "";
}

function heroImageSrc(url: string): string {
  return `/map/hero-image?url=${encodeURIComponent(url)}`;
}

function normalizeRuntimeConfig(config: RuntimeConfig | null | undefined): RuntimeConfig {
  return {
    mode: config?.mode || DEFAULT_RUNTIME_CONFIG.mode,
    features: {
      exports: config?.features?.exports ?? DEFAULT_RUNTIME_CONFIG.features.exports,
      rag: config?.features?.rag ?? DEFAULT_RUNTIME_CONFIG.features.rag,
      connectors: config?.features?.connectors ?? DEFAULT_RUNTIME_CONFIG.features.connectors,
      geocode: config?.features?.geocode ?? DEFAULT_RUNTIME_CONFIG.features.geocode,
    },
    map: {
      satelliteTileTemplate:
        config?.map?.satelliteTileTemplate || DEFAULT_RUNTIME_CONFIG.map.satelliteTileTemplate,
      satelliteTileSize:
        typeof config?.map?.satelliteTileSize === "number" && config.map.satelliteTileSize > 0
          ? config.map.satelliteTileSize
          : DEFAULT_RUNTIME_CONFIG.map.satelliteTileSize,
      satelliteAttribution:
        config?.map?.satelliteAttribution || DEFAULT_RUNTIME_CONFIG.map.satelliteAttribution,
      propertyOverlayTemplate:
        config?.map?.propertyOverlayTemplate
        || DEFAULT_RUNTIME_CONFIG.map.propertyOverlayTemplate,
      footfallProvider: {
        provider:
          config?.map?.footfallProvider?.provider
          || DEFAULT_RUNTIME_CONFIG.map.footfallProvider?.provider
          || "public_open_data",
        configured: Boolean(config?.map?.footfallProvider?.configured),
        requiresApiKey: Boolean(
          config?.map?.footfallProvider?.requiresApiKey
          ?? DEFAULT_RUNTIME_CONFIG.map.footfallProvider?.requiresApiKey,
        ),
        endpointConfigured: Boolean(config?.map?.footfallProvider?.endpointConfigured),
      },
    },
    localization: {
      defaultLocale:
        config?.localization?.defaultLocale
        || DEFAULT_RUNTIME_CONFIG.localization?.defaultLocale
        || "en",
      supportedLocales:
        config?.localization?.supportedLocales?.length
          ? config.localization.supportedLocales
          : DEFAULT_RUNTIME_CONFIG.localization?.supportedLocales || ["en", "zh"],
    },
    auth: {
      enabled: Boolean(config?.auth?.enabled ?? DEFAULT_RUNTIME_CONFIG.auth?.enabled),
      guestClickLimit:
        typeof config?.auth?.guestClickLimit === "number" && config.auth.guestClickLimit > 0
          ? config.auth.guestClickLimit
          : DEFAULT_RUNTIME_CONFIG.auth?.guestClickLimit || 10,
      usernameHint:
        config?.auth?.usernameHint || DEFAULT_RUNTIME_CONFIG.auth?.usernameHint || "visitor",
    },
    serviceRequests: {
      enabled: Boolean(
        config?.serviceRequests?.enabled ?? DEFAULT_RUNTIME_CONFIG.serviceRequests?.enabled,
      ),
      dailyLimit:
        config?.serviceRequests?.dailyLimit || DEFAULT_RUNTIME_CONFIG.serviceRequests?.dailyLimit || 10,
      types:
        config?.serviceRequests?.types?.length
          ? config.serviceRequests.types
          : DEFAULT_RUNTIME_CONFIG.serviceRequests?.types || [],
      updatesLimit:
        config?.serviceRequests?.updatesLimit
        || DEFAULT_RUNTIME_CONFIG.serviceRequests?.updatesLimit
        || 30,
    },
  };
}

function loadCountryPolygons(): CountryFeature[] {
  const collection = topoFeature(
    countries110m as unknown as Parameters<typeof topoFeature>[0],
    (countries110m as { objects: { countries: unknown } }).objects.countries as never,
  ) as unknown as FeatureCollection<Geometry, { name?: string }>;
  return collection.features.filter((item) => Boolean(countryName(item)));
}

function countryName(country: CountryFeature | null | undefined): string {
  return country?.properties?.name || "";
}

function countryPolygonVisualState(
  country: CountryFeature,
  selectedCountry: string,
  hoveredCountry: string,
  countriesWithData: Set<string>,
): { capColor: string; sideColor: string; strokeColor: string; altitude: number } {
  const name = countryName(country);
  if (countriesEquivalentForPolygon(name, selectedCountry)) {
    return {
      capColor: "rgba(238, 252, 249, 0.1)",
      sideColor: "rgba(85, 241, 209, 0.16)",
      strokeColor: "rgba(255, 255, 255, 0.96)",
      altitude: 0.03,
    };
  }
  if (countriesEquivalentForPolygon(name, hoveredCountry)) {
    return {
      capColor: "rgba(238, 252, 249, 0.08)",
      sideColor: "rgba(85, 241, 209, 0.12)",
      strokeColor: "rgba(255, 255, 255, 0.9)",
      altitude: 0.02,
    };
  }
  if (countrySetHasEquivalent(countriesWithData, name)) {
    return {
      capColor: "rgba(238, 252, 249, 0.012)",
      sideColor: "rgba(32, 50, 48, 0.12)",
      strokeColor: "rgba(238, 252, 249, 0.38)",
      altitude: 0.01,
    };
  }
  return {
    capColor: "rgba(255, 255, 255, 0.048)",
    sideColor: "rgba(32, 50, 48, 0.14)",
    strokeColor: "rgba(255, 255, 255, 0.13)",
    altitude: 0.008,
  };
}

function aggregateCountryMarkers(
  summaries: CountrySummary[],
  features: MapFeature[],
  displayPositions: Map<string, { lat: number; lng: number; positionSource: CountryPositionSource }>,
): CountryMarker[] {
  type CountryAccumulator = CountryMarker & {
    latTotal: number;
    lngTotal: number;
    mappedCities: Set<string>;
  };

  const countryMap = new Map<string, CountryAccumulator>();
  summaries.forEach((summary) => {
    const key = countryKey(summary.country);
    countryMap.set(key, {
      kind: "country" as const,
      key,
      country: summary.country,
      label: summary.country,
      lat: 0,
      lng: 0,
      positionSource: "default_fallback",
      latTotal: 0,
      lngTotal: 0,
      candidateCount: summary.candidate_count,
      mapReadyCount: summary.map_point_count,
      mappedCityCount: 0,
      reviewCount: summary.review_count,
      sourceCount: summary.source_count,
      scenes: { ...summary.scenes },
      propertyIds: [],
      mappedCities: new Set<string>(),
    });
  });

  features.forEach((feature) => {
    const country = feature.properties.country;
    const key = countryKey(country);
    const current = countryMap.get(key);
    if (!current) {
      return;
    }
    const [lng, lat] = feature.geometry.coordinates;
    current.lngTotal += lng;
    current.latTotal += lat;
    current.propertyIds.push(feature.properties.property_id);
    current.mappedCities.add(
      cityKey(country, feature.properties.city, feature.properties.city_id),
    );
    const mappedCount = current.propertyIds.length;
    current.lng = current.lngTotal / mappedCount;
    current.lat = current.latTotal / mappedCount;
    current.positionSource = "property_average";
  });

  return Array.from(countryMap.values())
    .map(({ latTotal: _latTotal, lngTotal: _lngTotal, mappedCities, ...marker }) => {
      const center = marker.propertyIds.length > 0
        ? {
            lat: marker.lat,
            lng: marker.lng,
            positionSource: "property_average" as const,
          }
        : countryDisplayPositionFromCache(marker.country, displayPositions);
      return {
        ...marker,
        lat: center.lat,
        lng: center.lng,
        positionSource: center.positionSource,
        mappedCityCount: mappedCities.size,
      };
    })
    .sort(compareMarkers);
}

function countryDisplayPositionFromCache(
  country: string,
  displayPositions: Map<string, { lat: number; lng: number; positionSource: CountryPositionSource }>,
): { lat: number; lng: number; positionSource: CountryPositionSource } {
  return displayPositions.get(country)
    || displayPositions.get(normalizeCountryName(country))
    || displayPositions.get(polygonCountryNameFor(country))
    || displayPositions.get(normalizeCountryName(polygonCountryNameFor(country)))
    || defaultCountryDisplayPosition(country);
}

function aggregateCityMarkers(
  packets: SitePacket[],
  features: MapFeature[],
  cityCentroidCache: Map<string, CityCentroidCacheValue>,
): CityMarker[] {
  type CityAccumulator = Omit<CityMarker, "sourceCount" | "positionSource"> & {
    latTotal: number;
    lngTotal: number;
    fallbackLatTotal: number;
    fallbackLngTotal: number;
    fallbackCount: number;
    evidenceCount: number;
    localities: Set<string>;
  };

  const featureByPropertyId = mapFeaturesByPropertyId(features);
  const cityMap = new Map<string, CityAccumulator>();
  packets.forEach((packet) => {
    const entity = packet.entity;
    if (!entity.city.trim()) {
      return;
    }
    const key = cityKey(entity.country, entity.city, entity.city_assignment?.city_id);
    const current = cityMap.get(key) ?? {
      kind: "city" as const,
      key,
      country: entity.country,
      city: entity.city,
      cityId: entity.city_assignment?.city_id,
      localityCount: 0,
      label: entity.city,
      lat: 0,
      lng: 0,
      latTotal: 0,
      lngTotal: 0,
      fallbackLatTotal: 0,
      fallbackLngTotal: 0,
      fallbackCount: 0,
      candidateCount: 0,
      mapReadyCount: 0,
      reviewCount: 0,
      scenes: {},
      propertyIds: [],
      evidenceCount: 0,
      localities: new Set<string>(),
    };

    current.candidateCount += 1;
    current.reviewCount += packet.review_queue.length;
    current.scenes[entity.scene_type] = (current.scenes[entity.scene_type] || 0) + 1;
    current.propertyIds.push(entity.property_id);
    current.evidenceCount += propertyEvidenceUnitCount(packet);
    const locality = entity.city_assignment?.locality?.trim();
    if (locality) {
      current.localities.add(locality);
      current.localityCount = current.localities.size;
    }

    if (Number.isFinite(entity.latitude) && Number.isFinite(entity.longitude)) {
      current.fallbackLngTotal += entity.longitude;
      current.fallbackLatTotal += entity.latitude;
      current.fallbackCount += 1;
    }

    const feature = featureByPropertyId.get(entity.property_id);
    if (feature) {
      const [lng, lat] = feature.geometry.coordinates;
      current.lngTotal += lng;
      current.latTotal += lat;
      current.mapReadyCount += 1;
      current.lng = current.lngTotal / current.mapReadyCount;
      current.lat = current.latTotal / current.mapReadyCount;
    } else if (isMapReadyEntity(entity) && Number.isFinite(entity.latitude) && Number.isFinite(entity.longitude)) {
      current.lngTotal += entity.longitude;
      current.latTotal += entity.latitude;
      current.mapReadyCount += 1;
      current.lng = current.lngTotal / current.mapReadyCount;
      current.lat = current.latTotal / current.mapReadyCount;
    }

    cityMap.set(key, current);
  });

  return Array.from(cityMap.values())
    .map(({
      latTotal: _latTotal,
      lngTotal: _lngTotal,
      fallbackLatTotal,
      fallbackLngTotal,
      fallbackCount,
      evidenceCount,
      localities: _localities,
      ...marker
    }): CityMarker | null => {
      if (marker.mapReadyCount > 0) {
        return {
          ...marker,
          positionSource: "map_ready_average" as const,
          sourceCount: evidenceCount,
        };
      }
      if (fallbackCount > 0) {
        return {
          ...marker,
          lat: fallbackLatTotal / fallbackCount,
          lng: fallbackLngTotal / fallbackCount,
          positionSource: "property_average" as const,
          sourceCount: evidenceCount,
        };
      }
      const centroid = cityCentroidCache.get(marker.key);
      if (!isUsableGeocodeResult(centroid)) {
        return null;
      }
      return {
        ...marker,
        lat: centroid.latitude,
        lng: centroid.longitude,
        positionSource: "city_geocode" as const,
        sourceCount: evidenceCount,
      };
    })
    .filter((marker): marker is CityMarker => marker !== null)
    .sort(compareMarkers);
}

function aggregateCitySummaryMarkers(summaries: CitySummary[]): CityMarker[] {
  return summaries
    .filter((summary) => {
      return summary.city.trim()
        && Number.isFinite(summary.lat)
        && Number.isFinite(summary.lng);
    })
    .map((summary) => ({
      kind: "city" as const,
      key: cityKey(summary.country, summary.city, summary.city_id),
      country: summary.country,
      city: summary.city,
      cityId: summary.city_id,
      localityCount: summary.locality_count || 0,
      label: summary.city,
      lat: summary.lat,
      lng: summary.lng,
      positionSource: summary.position_source || "property_average",
      candidateCount: summary.candidate_count,
      mapReadyCount: summary.map_point_count,
      reviewCount: summary.review_count,
      sourceCount: summary.source_count,
      scenes: summary.scenes || {},
      propertyIds: summary.property_ids || [],
    }))
    .sort(compareMarkers);
}

function mapFeaturesByPropertyId(features: MapFeature[]): Map<string, MapFeature> {
  return new Map(features.map((feature) => [feature.properties.property_id, feature]));
}

function citiesNeedingCentroids(
  packets: SitePacket[],
  features: MapFeature[],
  selectedCountry: string,
  cityCentroidCache: Map<string, CityCentroidCacheValue>,
): Array<{ key: string; country: string; city: string }> {
  const featureByPropertyId = mapFeaturesByPropertyId(features);
  const cityMap = new Map<string, { key: string; country: string; city: string; mapReadyCount: number }>();
  packets.forEach((packet) => {
    const entity = packet.entity;
    if (entity.country !== selectedCountry || !entity.city.trim()) {
      return;
    }
    const key = cityKey(entity.country, entity.city, entity.city_assignment?.city_id);
    const current = cityMap.get(key) ?? {
      key,
      country: entity.country,
      city: entity.city,
      mapReadyCount: 0,
    };
    if (
      featureByPropertyId.has(entity.property_id)
      || (isMapReadyEntity(entity) && Number.isFinite(entity.latitude) && Number.isFinite(entity.longitude))
    ) {
      current.mapReadyCount += 1;
    }
    cityMap.set(key, current);
  });
  return Array.from(cityMap.values()).filter((city) => {
    return city.mapReadyCount === 0 && !cityCentroidCache.has(city.key);
  });
}

function isUsableGeocodeResult(value: CityCentroidCacheValue | undefined): value is GeocodeResult {
  return Boolean(
    value
      && value !== "missing"
      && value !== "loading"
      && Number.isFinite(value.latitude)
      && Number.isFinite(value.longitude),
  );
}

function isMapReadyEntity(entity: Entity): boolean {
  return entity.coordinate_status.toLowerCase() === "map ready";
}

function deriveViewMode(
  selectedCountry: string,
  selectedCityKey: string,
  selectedPropertyId: string,
): ViewMode {
  if (selectedPropertyId) {
    return "property";
  }
  if (selectedCityKey) {
    return "city";
  }
  if (selectedCountry) {
    return "country";
  }
  return "overview";
}

function deriveGeoVisualMode(viewMode: ViewMode): GeoVisualMode {
  if (viewMode === "property") {
    return "property_satellite";
  }
  if (viewMode === "city") {
    return "city_satellite";
  }
  if (viewMode === "country") {
    return "country_satellite";
  }
  return "globe";
}

function satelliteCameraForScope({
  mode,
  selectedCountry,
  selectedCityMarker,
  selectedPacket,
  cityMarkers,
  visiblePackets,
  metricFilteredPackets,
  features,
  countryPolygons,
}: {
  mode: GeoVisualMode;
  selectedCountry: string;
  selectedCityMarker: CityMarker | null;
  selectedPacket: SitePacket | null;
  cityMarkers: CityMarker[];
  visiblePackets: SitePacket[];
  metricFilteredPackets: SitePacket[];
  features: MapFeature[];
  countryPolygons: CountryFeature[];
}): SatelliteCamera {
  if (mode === "property_satellite" && selectedPacket && isFiniteCoordinate(selectedPacket.entity)) {
    return {
      center: [selectedPacket.entity.longitude, selectedPacket.entity.latitude],
      zoom: 17,
      pitch: 60,
      bearing: -22,
    };
  }

  if (mode === "city_satellite") {
    const bounds = satelliteBoundsFromPackets(visiblePackets);
    if (bounds) {
      return cameraFromBounds(bounds, { pitch: 52, bearing: -18, minZoom: 11, maxZoom: 14.5 });
    }
    if (selectedCityMarker) {
      return {
        center: [selectedCityMarker.lng, selectedCityMarker.lat],
        zoom: 12.5,
        pitch: 52,
        bearing: -18,
      };
    }
  }

  if (mode === "country_satellite") {
    const cityBounds = satelliteBoundsFromMarkers(
      cityMarkers.filter((marker) => marker.country === selectedCountry),
    );
    const propertyBounds = satelliteBoundsFromPackets(
      metricFilteredPackets.filter((packet) => packet.entity.country === selectedCountry),
    );
    const bounds = cityBounds || propertyBounds;
    if (bounds) {
      return cameraFromBounds(bounds, { pitch: 45, bearing: -14, minZoom: 4.4, maxZoom: 7.6 });
    }
    const center = countryFocus(selectedCountry, features, countryPolygons);
    return {
      center: [center.lng, center.lat],
      zoom: 5.4,
      pitch: 45,
      bearing: -14,
    };
  }

  const center = propertyFocus(features);
  return {
    center: [center.lng, center.lat],
    zoom: 2.8,
    pitch: 0,
    bearing: 0,
  };
}

function satelliteMarkersForScope({
  mode,
  selectedCountry,
  selectedCityKey,
  selectedPropertyId,
  selectedPacket,
  cityMarkers,
  visiblePackets,
  metricFilteredPackets,
}: {
  mode: GeoVisualMode;
  selectedCountry: string;
  selectedCityKey: string;
  selectedPropertyId: string;
  selectedPacket: SitePacket | null;
  cityMarkers: CityMarker[];
  visiblePackets: SitePacket[];
  metricFilteredPackets: SitePacket[];
}): SatelliteMarker[] {
  if (mode === "country_satellite") {
    const cities = cityMarkers.filter((marker) => marker.country === selectedCountry);
    if (cities.length > 0) {
      return cities.map((marker) => ({
        id: marker.key,
        kind: "city",
        lat: marker.lat,
        lng: marker.lng,
        label: marker.city,
        country: marker.country,
        city: marker.city,
        count: marker.candidateCount,
        selected: marker.key === selectedCityKey,
        meta: `${formatNumber(marker.candidateCount)} candidates`,
        candidateCount: marker.candidateCount,
        sourceCount: marker.sourceCount,
        reviewCount: marker.reviewCount,
        scenes: marker.scenes,
      }));
    }
    return [];
  }
  if (mode === "city_satellite") {
    return visiblePackets
      .filter((packet) => isFiniteCoordinate(packet.entity))
      .map((packet) => propertySatelliteMarker(packet, selectedPropertyId));
  }
  if (mode === "property_satellite" && selectedPacket && isFiniteCoordinate(selectedPacket.entity)) {
    return [propertySatelliteMarker(selectedPacket, selectedPacket.entity.property_id)];
  }
  return [];
}

function propertySatelliteMarker(packet: SitePacket, selectedPropertyId: string): SatelliteMarker {
  return {
    id: packet.entity.property_id,
    kind: "property",
    lat: packet.entity.latitude,
    lng: packet.entity.longitude,
    label: packet.entity.property_name,
    country: packet.entity.country,
    city: packet.entity.city,
    selected: packet.entity.property_id === selectedPropertyId,
    meta: sceneLabel(packet.entity.scene_type),
    candidateCount: 1,
    sourceCount: propertyEvidenceUnitCount(packet),
    reviewCount: packet.review_queue.length,
    scenes: { [packet.entity.scene_type]: 1 },
  };
}

function satelliteBoundsFromPackets(packets: SitePacket[]): SatelliteBounds | null {
  return boundsFromCoordinates(
    packets
      .filter((packet) => isFiniteCoordinate(packet.entity))
      .map((packet) => ({ lat: packet.entity.latitude, lng: packet.entity.longitude })),
  );
}

function satelliteBoundsFromMarkers(markers: CityMarker[]): SatelliteBounds | null {
  return boundsFromCoordinates(markers.map((marker) => ({ lat: marker.lat, lng: marker.lng })));
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

function isFiniteCoordinate(entity: Entity): boolean {
  return Number.isFinite(entity.latitude) && Number.isFinite(entity.longitude);
}

function summarizeEvidenceGaps(packets: SitePacket[]): EvidenceGapSummary {
  return packets.reduce<EvidenceGapSummary>(
    (summary, packet) => {
      if (!primaryMetricEvidence(packet)) {
        summary.missingPrimaryMetricCount += 1;
      }
      if (!packet.entity.hero_image) {
        summary.missingImageCount += 1;
      }
      if (packet.conclusion.evidence_status === "Insufficient") {
        summary.lowEvidenceCount += 1;
      }
      if (packet.entity.coordinate_status === "Review Required") {
        summary.coordinateReviewCount += 1;
      }
      summary.reviewQueueCount += packet.review_queue.length;
      return summary;
    },
    {
      missingPrimaryMetricCount: 0,
      missingImageCount: 0,
      lowEvidenceCount: 0,
      coordinateReviewCount: 0,
      reviewQueueCount: 0,
    },
  );
}

function workspaceContextLabel({
  selectedCountry,
  selectedCityMarker,
  panelSceneFilter,
  sceneFilter,
  evidenceFilter,
  actionFilter,
  reviewOnly,
  localization,
}: {
  selectedCountry: string;
  selectedCityMarker: CityMarker | null;
  panelSceneFilter: string;
  sceneFilter: string;
  evidenceFilter: string;
  actionFilter: string;
  reviewOnly: boolean;
  localization: LocalizationPayload;
}): string {
  const parts = [
    selectedCityMarker
      ? `${selectedCityMarker.city}, ${selectedCityMarker.country}`
      : selectedCountry || uiLabel(localization, "ui.all_countries", "All countries"),
  ];
  if (panelSceneFilter) {
    parts.push(`${sceneLabel(panelSceneFilter, localization)} list`);
  }
  if (sceneFilter) {
    parts.push(`${sceneLabel(sceneFilter, localization)} API filter`);
  }
  if (evidenceFilter) {
    parts.push(evidenceFilter);
  }
  if (actionFilter) {
    parts.push(actionFilter);
  }
  if (reviewOnly) {
    parts.push(uiLabel(localization, "ui.review", "Review"));
  }
  return parts.join(" · ");
}

function evidenceSourceCount(packet: SitePacket): number {
  return propertyEvidenceUnitCount(packet);
}

function networkExperienceMetrics(
  packet: SitePacket,
  localization?: LocalizationPayload,
): NetworkExperienceSummary {
  return packet.evidence
    .filter((item) => isNetworkExperienceEvidence(item))
    .reduce<NetworkExperienceSummary>((summary, item) => {
      const kind = networkExperienceKind(item);
      if (!kind) {
        return summary;
      }
      const metric: NetworkExperienceMetric = {
        kind,
        label: kind === "mobile"
          ? uiLabel(localization, "ui.mobile_network", "Mobile")
          : uiLabel(localization, "ui.fixed_network", "Fixed"),
        downloadMbps: matchNumber(item.field_value, /avg download\s+([\d,.]+)\s*Mbps/i),
        uploadMbps: matchNumber(item.field_value, /avg upload\s+([\d,.]+)\s*Mbps/i),
        latencyMs: matchNumber(item.field_value, /avg latency\s+([\d,.]+)\s*ms/i),
        loadedLatencyDownMs: matchNumber(item.field_value, /loaded-down\s+([\d,.]+)\s*ms/i),
        loadedLatencyUpMs: matchNumber(item.field_value, /loaded-up\s+([\d,.]+)\s*ms/i),
        tests: matchNumber(item.field_value, /(\d+(?:,\d{3})*)\s+tests?\s+from/i),
        devices: matchNumber(item.field_value, /from\s+(\d+(?:,\d{3})*)\s+devices?/i),
        distanceM: matchNumber(item.field_value, /tile centroid\s+([\d,.]+)\s*m/i),
        confidence: networkExperienceConfidence(item),
        fieldValue: item.field_value,
        sourceName: item.source_name,
        sourceUrl: item.source_url,
        sourceDate: item.source_date,
      };
      summary[kind] = metric;
      return summary;
    }, {});
}

function networkExperienceCardSummary(
  packet: SitePacket,
  localization?: LocalizationPayload,
): string {
  const experience = networkExperienceMetrics(packet, localization);
  const parts = [experience.mobile, experience.fixed]
    .filter(Boolean)
    .map((metric) => compactNetworkMetric(metric as NetworkExperienceMetric, localization));
  if (parts.length === 0) {
    return "";
  }
  return `${uiLabel(localization, "ui.network_experience", "Network Experience")}: ${parts.join(" · ")}`;
}

function compactNetworkMetric(
  metric: NetworkExperienceMetric,
  localization?: LocalizationPayload,
): string {
  const speed = metric.downloadMbps === null
    ? uiLabel(localization, "fallback.unknown", "Unknown")
    : `${formatNullableNumber(metric.downloadMbps, localization?.locale || "en")} Mbps`;
  const latency = metric.latencyMs === null
    ? ""
    : ` / ${formatNullableNumber(metric.latencyMs, localization?.locale || "en")} ms`;
  return `${metric.label} ${speed}${latency}`;
}

function isNetworkExperienceEvidence(item: EvidenceItem): boolean {
  return normalizeMetricKey(item.field_group) === "network_experience"
    || normalizeMetricKey(item.indicator_name || "").startsWith("ookla_");
}

function networkExperienceKind(item: EvidenceItem): "mobile" | "fixed" | null {
  const key = `${item.indicator_name || ""} ${item.field_value}`.toLowerCase();
  if (key.includes("mobile")) {
    return "mobile";
  }
  if (key.includes("fixed")) {
    return "fixed";
  }
  return null;
}

function networkExperienceConfidence(
  item: EvidenceItem,
): "high" | "medium" | "low" | "unknown" {
  const text = `${item.field_value} ${item.assumption_note || ""}`.toLowerCase();
  if (text.includes("confidence=high") || text.includes("proxy confidence high")) {
    return "high";
  }
  if (text.includes("confidence=medium") || text.includes("proxy confidence medium")) {
    return "medium";
  }
  if (text.includes("confidence=low") || text.includes("proxy confidence low")) {
    return "low";
  }
  return "unknown";
}

function networkConfidenceLabel(
  confidence: NetworkExperienceMetric["confidence"],
  localization?: LocalizationPayload,
): string {
  const labels: Record<NetworkExperienceMetric["confidence"], string> = {
    high: uiLabel(localization, "ui.high_confidence", "High confidence"),
    medium: uiLabel(localization, "ui.medium_confidence", "Medium confidence"),
    low: uiLabel(localization, "ui.low_confidence", "Low confidence"),
    unknown: uiLabel(localization, "fallback.unknown", "Unknown"),
  };
  return labels[confidence];
}

function complaintPressureLabel(
  pressure: string,
  localization?: LocalizationPayload,
): string {
  const normalized = pressure.trim().toLowerCase();
  const labels: Record<string, string> = {
    high: uiLabel(localization, "ui.high", "High"),
    moderate: uiLabel(localization, "ui.moderate", "Moderate"),
    low: uiLabel(localization, "ui.low", "Low"),
    insufficient: uiLabel(localization, "ui.complaint_insufficient", "Insufficient"),
  };
  return labels[normalized] || enumLabel(pressure, localization);
}

function complaintCategoryLabel(
  category: string,
  localization?: LocalizationPayload,
): string {
  const key = normalizeMetricKey(category);
  const labels: Record<string, string> = {
    no_signal: uiLabel(localization, "ui.complaint_no_signal", "No signal"),
    weak_signal: uiLabel(localization, "ui.complaint_weak_coverage", "Weak coverage"),
    weak_coverage: uiLabel(localization, "ui.complaint_weak_coverage", "Weak coverage"),
    slow_data: uiLabel(localization, "ui.complaint_slow_data", "Slow data"),
    dropped_call: uiLabel(localization, "ui.complaint_dropped_call", "Dropped calls"),
    network_outage: uiLabel(localization, "ui.complaint_outage", "Network outage"),
    outage: uiLabel(localization, "ui.complaint_outage", "Network outage"),
  };
  return labels[key] || enumLabel(category, localization);
}

function formatComplaintDate(
  value: string | null | undefined,
  locale: string,
  localization?: LocalizationPayload,
): string {
  if (!value) {
    return uiLabel(localization, "fallback.unknown", "Unknown");
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en", {
    dateStyle: "medium",
  }).format(date);
}

function networkSampleLabel(
  metric: NetworkExperienceMetric,
  locale: string,
  localization?: LocalizationPayload,
): string {
  return uiLabel(localization, "ui.tests_devices", "{tests} tests / {devices} devices", {
    tests: formatNullableNumber(metric.tests, locale),
    devices: formatNullableNumber(metric.devices, locale),
  });
}

function matchNumber(text: string, pattern: RegExp): number | null {
  const match = text.match(pattern);
  return match ? parseMetricNumber(match[1]) : null;
}

function formatNullableNumber(value: number | null | undefined, locale = "en"): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return locale === "zh" ? "未知" : "Unknown";
  }
  return formatNumber(value, locale);
}

function propertyEvidenceUnitCount(packet: SitePacket): number {
  return Math.max(1, packet.evidence.length);
}

function localizedEvidenceItem(packet: SitePacket, item: EvidenceItem): LocalizedEvidenceItem | undefined {
  const index = packet.evidence.indexOf(item);
  return index >= 0 ? packet.localized?.evidence?.[index] : undefined;
}

function localizedPrimaryMetricText(packet: SitePacket, localization?: LocalizationPayload): string {
  return packet.localized?.primary_metric?.display_text
    || uiLabel(localization, "fallback.localization_pending", "Localization pending");
}

function localizedReasonToRecommend(packet: SitePacket, localization: LocalizationPayload): string {
  return packet.localized?.conclusion?.reason_to_recommend
    || uiLabel(localization, "fallback.localization_pending", "Localization pending");
}

function localizedNextAction(packet: SitePacket, localization: LocalizationPayload): string {
  return packet.localized?.conclusion?.next_action
    || uiLabel(localization, "fallback.localization_pending", "Localization pending");
}

function localizedInferenceItem(
  packet: SitePacket,
  item: InferenceRecord,
): Record<string, string | undefined> | undefined {
  const index = packet.inference.indexOf(item);
  return index >= 0 ? packet.localized?.inference?.[index] : undefined;
}

function localizedReviewItem(
  packet: SitePacket,
  item: ReviewItem,
): Record<string, string | undefined> | undefined {
  const index = packet.review_queue.indexOf(item);
  return index >= 0 ? packet.localized?.review_queue?.[index] : undefined;
}

function filterAndSortPanelPackets(packets: SitePacket[], scene: string): SitePacket[] {
  return (scene
    ? packets.filter((packet) => packet.entity.scene_type === scene)
    : packets)
    .slice()
    .sort(comparePacketsByDisplayPriority);
}

function comparePacketsByDisplayPriority(a: SitePacket, b: SitePacket): number {
  const imagePriority = Number(Boolean(b.entity.hero_image)) - Number(Boolean(a.entity.hero_image));
  return imagePriority || comparePacketsByPrimaryMetricDesc(a, b);
}

function comparePacketsByPrimaryMetricDesc(a: SitePacket, b: SitePacket): number {
  const aValue = primaryMetricValue(a);
  const bValue = primaryMetricValue(b);
  if (aValue !== null && bValue !== null && aValue !== bValue) {
    return bValue - aValue;
  }
  if (aValue !== null && bValue === null) {
    return -1;
  }
  if (aValue === null && bValue !== null) {
    return 1;
  }
  const nameCompare = a.entity.property_name.localeCompare(b.entity.property_name);
  if (nameCompare !== 0) {
    return nameCompare;
  }
  return a.entity.city.localeCompare(b.entity.city);
}

function primaryMetricValue(packet: SitePacket): number | null {
  const metricEvidence = primaryMetricEvidence(packet);
  if (!metricEvidence) {
    return null;
  }
  return numericMetricValue(metricEvidence.field_value);
}

function primaryMetricEvidence(packet: SitePacket): EvidenceItem | null {
  const config = SCENE_PRIMARY_METRIC_FILTERS[packet.entity.scene_type];
  const candidateEvidence = config
    ? packet.evidence.filter((item) => evidenceMatchesPrimaryMetric(item, config))
    : packet.evidence;
  const rankedEvidence = candidateEvidence
    .map((item, index) => ({
      item,
      index,
      rank: metricIndicatorRank(item, config),
      value: numericMetricValue(item.field_value),
    }))
    .filter((entry) => entry.value !== null && Number.isFinite(entry.value));
  if (rankedEvidence.length === 0) {
    return null;
  }
  rankedEvidence.sort((a, b) =>
    a.rank - b.rank
    || (b.value ?? 0) - (a.value ?? 0)
    || a.index - b.index,
  );
  return rankedEvidence[0].item;
}

function metricIndicatorRank(
  evidence: EvidenceItem,
  config: ScenePrimaryMetricFilter | undefined,
): number {
  if (!config) {
    return 0;
  }
  const keys = [
    normalizeMetricKey(evidence.field_group),
    normalizeMetricKey(evidence.indicator_name || ""),
  ];
  const ranks = config.indicators.map(normalizeMetricKey);
  const matched = keys
    .map((key) => ranks.indexOf(key))
    .filter((index) => index >= 0);
  return matched.length > 0 ? Math.min(...matched) : ranks.length;
}

function evidenceMatchesPrimaryMetric(
  evidence: EvidenceItem,
  config: ScenePrimaryMetricFilter,
): boolean {
  const keys = new Set(config.indicators.map(normalizeMetricKey));
  return keys.has(normalizeMetricKey(evidence.field_group))
    || keys.has(normalizeMetricKey(evidence.indicator_name || ""));
}

function numericMetricValue(text: string): number | null {
  const values: number[] = [];
  const scaledPattern = /(\d+(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(billion|bn|million|mn|thousand|k)\b/gi;
  for (const match of text.matchAll(scaledPattern)) {
    values.push(parseMetricNumber(match[1]) * metricMultiplier(match[2]));
  }

  const numberPattern = /\d+(?:,\d{3})*(?:\.\d+)?/g;
  for (const match of text.matchAll(numberPattern)) {
    values.push(parseMetricNumber(match[0]));
  }

  const finiteValues = values.filter((value) => Number.isFinite(value) && value > 0);
  if (finiteValues.length === 0) {
    return null;
  }
  const nonYearValues = finiteValues.filter((value) => value < 1900 || value > 2035);
  return Math.max(...(nonYearValues.length > 0 ? nonYearValues : finiteValues));
}

function parseMetricNumber(value: string): number {
  return Number(value.replaceAll(",", ""));
}

function metricMultiplier(unit: string): number {
  const normalized = unit.toLowerCase();
  if (normalized === "billion" || normalized === "bn") {
    return 1_000_000_000;
  }
  if (normalized === "million" || normalized === "mn") {
    return 1_000_000;
  }
  if (normalized === "thousand" || normalized === "k") {
    return 1_000;
  }
  return 1;
}

function primaryMetricFilterLabel(scene: string, localization?: LocalizationPayload): string {
  const label = SCENE_PRIMARY_METRIC_FILTERS[scene]?.label || "Primary metric";
  return label === "Primary metric"
    ? uiLabel(localization, "ui.primary_metric", "Primary metric")
    : label;
}

function normalizeMetricKey(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function compareMarkers(a: GlobeMarker, b: GlobeMarker): number {
  if (b.candidateCount !== a.candidateCount) {
    return b.candidateCount - a.candidateCount;
  }
  return `${a.country} ${a.label}`.localeCompare(`${b.country} ${b.label}`);
}

function countryKey(country: string): string {
  return `country::${country.trim().toLowerCase()}`;
}

function cityKey(country: string, city: string, cityId?: string | null): string {
  return cityId?.trim() || `${country.trim().toLowerCase()}::${city.trim().toLowerCase()}`;
}

function cityFocus(marker: CityMarker): { lat: number; lng: number } {
  return { lat: marker.lat, lng: marker.lng };
}

function globeMarkerLabel(marker: DisplayGlobeMarker): string {
  const topScenes = Object.entries(marker.scenes)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 2)
    .map(([scene, count]) => `${sceneLabel(scene)} ${count}`)
    .join(" · ");
  if (marker.kind === "city_cluster") {
    return [
      `${formatNumber(marker.cityCount)} cities in ${marker.country}`,
      `${formatNumber(marker.candidateCount)} candidates`,
      `${formatNumber(marker.reviewCount)} review`,
      `${formatNumber(marker.sourceCount)} evidence`,
      "Click to expand city markers",
      topScenes,
    ].filter(Boolean).join("\n");
  }
  const mappedText = marker.kind === "country"
    ? marker.positionSource === "display_anchor"
      ? "country display anchor"
      : marker.positionSource === "default_fallback"
        ? "fallback country position"
      : `${formatNumber(marker.mappedCityCount)} mapped cities`
    : marker.positionSource === "city_geocode"
      ? "estimated city centroid"
      : marker.positionSource === "property_average"
        ? "estimated city position"
      : `${formatNumber(marker.mapReadyCount)} mapped`;
  return [
    marker.kind === "country" ? marker.country : `${marker.city}, ${marker.country}`,
    `${formatNumber(marker.candidateCount)} candidates`,
    mappedText,
    `${formatNumber(marker.reviewCount)} review`,
    topScenes,
  ].filter(Boolean).join("\n");
}

function globeMarkerAriaLabel(marker: DisplayGlobeMarker): string {
  if (marker.kind === "country") {
    const sourceText = marker.positionSource === "display_anchor" ? ", country display marker" : "";
    return `${marker.country}, ${formatNumber(marker.candidateCount)} candidate properties${sourceText}`;
  }
  if (marker.kind === "city_cluster") {
    return `${formatNumber(marker.cityCount)} clustered cities in ${marker.country}, ${formatNumber(marker.candidateCount)} candidate properties, click to expand`;
  }
  return `${marker.city}, ${marker.country}, ${formatNumber(marker.candidateCount)} candidate properties`;
}

function globeMarkerHoverInfo(marker: DisplayGlobeMarker): MapHoverMarker {
  if (marker.kind === "city_cluster") {
    return {
      title: `${formatNumber(marker.cityCount)} cities`,
      detail: `${formatNumber(marker.candidateCount)} candidates`,
      variant: "cluster",
    };
  }
  if (marker.kind === "country") {
    return {
      title: marker.country,
      detail: `${formatNumber(marker.candidateCount)} candidates`,
      variant: "country",
    };
  }
  return {
    title: marker.city,
    detail: `${formatNumber(marker.candidateCount)} candidates`,
    variant: "city",
  };
}

function globeMarkerPositionSource(marker: DisplayGlobeMarker): string {
  if (marker.kind === "country") {
    return marker.positionSource;
  }
  if (marker.kind === "city_cluster") {
    return "city_cluster";
  }
  return marker.positionSource || "city_marker";
}

function GlobeMarkerButton({
  marker,
  active,
  hovered,
  onSelect,
  onHover,
  spiderChild,
  style,
}: {
  marker: DisplayGlobeMarker;
  active: boolean;
  hovered?: boolean;
  onSelect: (marker: DisplayGlobeMarker) => void;
  onHover?: (marker: DisplayGlobeMarker | null) => void;
  spiderChild?: boolean;
  style?: React.CSSProperties;
}) {
  const markerClass = marker.kind === "city_cluster" ? "cluster-marker" : `${marker.kind}-marker`;
  const sourceClass = marker.kind === "country" && marker.positionSource === "display_anchor"
    ? "micro-country-marker"
    : "";
  const hoverClass = hovered ? "hovered" : "";
  return (
    <button
      className={`city-marker globe-marker ${markerClass} ${sourceClass} ${hoverClass} ${active ? "active" : ""} ${spiderChild ? "spider-child" : ""}`}
      type="button"
      aria-label={globeMarkerAriaLabel(marker)}
      data-marker-detail={globeMarkerLabel(marker)}
      data-marker-kind={marker.kind}
      data-position-source={globeMarkerPositionSource(marker)}
      style={style}
      onMouseEnter={() => {
        onHover?.(marker);
      }}
      onMouseLeave={() => {
        onHover?.(null);
      }}
      onFocus={() => {
        onHover?.(marker);
      }}
      onBlur={() => {
        onHover?.(null);
      }}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(marker);
      }}
    >
      <span className="city-marker-ring" aria-hidden="true" />
      <span className="city-marker-core">{formatNumber(marker.candidateCount)}</span>
      <span className="city-marker-label">
        {marker.kind === "city_cluster" ? `${formatNumber(marker.cityCount)} cities` : marker.label}
      </span>
    </button>
  );
}

function configureGlobeControls(globe: GlobeMethods | null, mode: GlobeMode) {
  const controls = globe?.controls();
  if (!controls) {
    return;
  }
  controls.enableDamping = true;
  controls.autoRotate = mode === "overview";
  controls.autoRotateSpeed = OVERVIEW_ROTATE_SPEED;
}

function projectGlobeMarkers(
  globe: GlobeProjectionMethods,
  markers: GlobeMarker[],
  selectedCityKey: string,
  pointOfViewOverride?: { lat: number; lng: number } | null,
): ScreenGlobeMarker[] {
  const pov = pointOfViewOverride || globe.pointOfView();
  return markers.map((marker) => {
    const isActiveCity = marker.kind === "city" && marker.key === selectedCityKey;
    const altitude = marker.kind === "country" ? 0.045 : isActiveCity ? 0.06 : 0.04;
    const coords = globe.getScreenCoords(marker.lat, marker.lng, altitude);
    return {
      ...marker,
      x: roundScreenCoordinate(coords.x),
      y: roundScreenCoordinate(coords.y - (isActiveCity ? 8 : 0)),
      visible: globeMarkerFacesCamera(marker, pov),
      spiderChild: false,
    };
  });
}

function projectMockGlobeMarkers(
  markers: GlobeMarker[],
  selectedCityKey: string,
  stageSize: { width: number; height: number },
  pointOfViewOverride?: { lat: number; lng: number } | null,
): ScreenGlobeMarker[] {
  const centerLat = average(markers.map((marker) => marker.lat));
  const centerLng = average(markers.map((marker) => marker.lng));
  const cityScale = 42;
  const countrySpacing = Math.min(170, Math.max(96, stageSize.width / Math.max(3, markers.length + 1)));

  return markers.map((marker, index) => {
    const isActiveCity = marker.kind === "city" && marker.key === selectedCityKey;
    const x = marker.kind === "city"
      ? stageSize.width / 2 + (marker.lng - centerLng) * cityScale
      : stageSize.width / 2 + (index - (markers.length - 1) / 2) * countrySpacing;
    const y = marker.kind === "city"
      ? stageSize.height / 2 - (marker.lat - centerLat) * cityScale
      : stageSize.height * 0.45;
    return {
      ...marker,
      x: roundScreenCoordinate(x),
      y: roundScreenCoordinate(y),
      visible: pointOfViewOverride ? globeMarkerFacesCamera(marker, pointOfViewOverride) : true,
      spiderChild: false,
      ...(isActiveCity ? { y: roundScreenCoordinate(y - 8) } : {}),
    };
  });
}

function clusterScreenGlobeMarkers(
  projected: ScreenGlobeMarker[],
  {
    expandedClusterKey,
    selectedCityKey,
    selectedCountry,
    stageSize,
  }: {
    expandedClusterKey: string;
    selectedCityKey: string;
    selectedCountry: string;
    stageSize: { width: number; height: number };
  },
): ScreenDisplayGlobeMarker[] {
  if (!selectedCountry) {
    return projected;
  }

  const passthrough: ScreenDisplayGlobeMarker[] = [];
  const clusterableCities: ScreenGlobeMarker[] = [];
  projected.forEach((marker) => {
    if (marker.kind !== "city" || !marker.visible || marker.key === selectedCityKey) {
      passthrough.push(marker);
      return;
    }
    clusterableCities.push(marker);
  });

  const clustered: ScreenDisplayGlobeMarker[] = [];
  buildScreenCityClusters(clusterableCities).forEach((cluster) => {
    if (cluster.length === 1) {
      clustered.push(cluster[0]);
      return;
    }
    const clusterMarker = makeCityClusterMarker(cluster);
    if (clusterMarker.key === expandedClusterKey) {
      clustered.push(...spiderCityCluster(cluster, clusterMarker, stageSize));
      return;
    }
    clustered.push(clusterMarker);
  });

  return [...passthrough, ...clustered];
}

function buildScreenCityClusters(markers: ScreenGlobeMarker[]): ScreenGlobeMarker[][] {
  const clusters: ScreenGlobeMarker[][] = [];
  markers.forEach((marker) => {
    let closestClusterIndex = -1;
    let closestDistance = Number.POSITIVE_INFINITY;
    clusters.forEach((cluster, index) => {
      const center = screenClusterCenter(cluster);
      const distance = screenDistance(marker, center);
      if (distance <= CITY_CLUSTER_RADIUS_PX && distance < closestDistance) {
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

function makeCityClusterMarker(cluster: ScreenGlobeMarker[]): ScreenCityClusterMarker {
  const center = screenClusterCenter(cluster);
  const cityKeys = cluster.map((marker) => marker.key).sort();
  const scenes: Record<string, number> = {};
  cluster.forEach((marker) => {
    Object.entries(marker.scenes).forEach(([scene, count]) => {
      scenes[scene] = (scenes[scene] || 0) + count;
    });
  });

  return {
    kind: "city_cluster",
    key: `city_cluster::${cityKeys.join("|")}`,
    country: cluster[0]?.country || "",
    label: `${formatNumber(cluster.length)} cities`,
    lat: average(cluster.map((marker) => marker.lat)),
    lng: average(cluster.map((marker) => marker.lng)),
    cityCount: cluster.length,
    candidateCount: cluster.reduce((total, marker) => total + marker.candidateCount, 0),
    mapReadyCount: cluster.reduce((total, marker) => total + marker.mapReadyCount, 0),
    reviewCount: cluster.reduce((total, marker) => total + marker.reviewCount, 0),
    sourceCount: cluster.reduce((total, marker) => total + marker.sourceCount, 0),
    scenes,
    propertyIds: cluster.flatMap((marker) => marker.propertyIds),
    cityKeys,
    x: center.x,
    y: center.y,
    visible: true,
  };
}

function spiderCityCluster(
  cluster: ScreenGlobeMarker[],
  clusterMarker: ScreenCityClusterMarker,
  stageSize: { width: number; height: number },
): ScreenGlobeMarker[] {
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

function screenClusterCenter(cluster: ScreenGlobeMarker[]): { x: number; y: number } {
  return {
    x: roundScreenCoordinate(average(cluster.map((marker) => marker.x))),
    y: roundScreenCoordinate(average(cluster.map((marker) => marker.y))),
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

function globeFocusFromMarkers(markers: CountryMarker[]): { lat: number; lng: number } | null {
  if (markers.length === 0) {
    return null;
  }
  return {
    lat: average(markers.map((marker) => marker.lat)),
    lng: averageLongitude(markers.map((marker) => marker.lng)),
  };
}

function averageLongitude(values: number[]): number {
  if (values.length === 0) {
    return 0;
  }
  const vector = values.reduce(
    (total, value) => ({
      sin: total.sin + Math.sin(toRadians(value)),
      cos: total.cos + Math.cos(toRadians(value)),
    }),
    { sin: 0, cos: 0 },
  );
  return toDegrees(Math.atan2(vector.sin / values.length, vector.cos / values.length));
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

function sameScreenGlobeMarkers(
  current: ScreenDisplayGlobeMarker[],
  next: ScreenDisplayGlobeMarker[],
): boolean {
  return current.length === next.length && current.every((marker, index) => {
    const nextMarker = next[index];
    return Boolean(nextMarker)
      && marker.key === nextMarker.key
      && marker.kind === nextMarker.kind
      && marker.x === nextMarker.x
      && marker.y === nextMarker.y
      && marker.visible === nextMarker.visible
      && marker.candidateCount === nextMarker.candidateCount
      && marker.spiderChild === nextMarker.spiderChild
      && markerCityCount(marker) === markerCityCount(nextMarker);
  });
}

function markerCityCount(marker: ScreenDisplayGlobeMarker): number {
  return marker.kind === "city_cluster" ? marker.cityCount : 1;
}

function globeMarkerFacesCamera(marker: GlobeMarker, pov: { lat: number; lng: number }): boolean {
  return angularDistanceDegrees(marker.lat, marker.lng, pov.lat, pov.lng) <= 98;
}

function angularDistanceDegrees(latA: number, lngA: number, latB: number, lngB: number): number {
  const phiA = toRadians(latA);
  const phiB = toRadians(latB);
  const deltaLng = toRadians(normalizeLongitudeDelta(lngB - lngA));
  const cosAngle =
    Math.sin(phiA) * Math.sin(phiB)
    + Math.cos(phiA) * Math.cos(phiB) * Math.cos(deltaLng);
  return toDegrees(Math.acos(Math.min(1, Math.max(-1, cosAngle))));
}

function normalizeLongitudeDelta(delta: number): number {
  return ((delta + 540) % 360) - 180;
}

function toRadians(value: number): number {
  return value * Math.PI / 180;
}

function toDegrees(value: number): number {
  return value * 180 / Math.PI;
}

function summaryFromPackets(
  packets: SitePacket[],
  marker: CityMarker | null,
): CountrySummary {
  if (packets.length === 0 && marker) {
    return summaryFromCityMarker(marker);
  }
  return packets.reduce<CountrySummary>(
    (total, packet) => {
      total.candidate_count += 1;
      total.map_point_count += 1;
      total.review_count += packet.review_queue.length;
      total.source_count += propertyEvidenceUnitCount(packet);
      total.scenes[packet.entity.scene_type] = (total.scenes[packet.entity.scene_type] || 0) + 1;
      return total;
    },
    {
      country: marker?.country || "",
      candidate_count: 0,
      map_point_count: 0,
      coordinate_review_count: 0,
      review_count: 0,
      source_count: 0,
      scenes: {},
    },
  );
}

function summaryFromCityMarker(marker: CityMarker): CountrySummary {
  return {
    country: marker.country,
    candidate_count: marker.candidateCount,
    map_point_count: marker.mapReadyCount,
    coordinate_review_count: Math.max(0, marker.candidateCount - marker.mapReadyCount),
    review_count: marker.reviewCount,
    source_count: marker.sourceCount,
    scenes: { ...marker.scenes },
  };
}

function summaryFromRegionSummary(region: RegionSummary): CountrySummary {
  return {
    country: region.region,
    candidate_count: region.candidate_count,
    map_point_count: region.map_point_count,
    coordinate_review_count: 0,
    review_count: region.review_count,
    source_count: region.source_count,
    scenes: { ...region.scenes },
  };
}

function aggregateSummary(summaries: CountrySummary[], selectedCountry: string): CountrySummary {
  const target = summaries.find((summary) => summary.country === selectedCountry);
  if (target) {
    return target;
  }
  if (selectedCountry) {
    return {
      country: selectedCountry,
      candidate_count: 0,
      map_point_count: 0,
      coordinate_review_count: 0,
      review_count: 0,
      source_count: 0,
      scenes: {},
    };
  }
  return summaries.reduce<CountrySummary>(
    (total, row) => {
      total.candidate_count += row.candidate_count;
      total.map_point_count += row.map_point_count;
      total.coordinate_review_count += row.coordinate_review_count;
      total.review_count += row.review_count;
      total.source_count += row.source_count;
      Object.entries(row.scenes).forEach(([scene, count]) => {
        total.scenes[scene] = (total.scenes[scene] || 0) + count;
      });
      return total;
    },
    {
      country: selectedCountry,
      candidate_count: 0,
      map_point_count: 0,
      coordinate_review_count: 0,
      review_count: 0,
      source_count: 0,
      scenes: {},
    },
  );
}

function aggregateRegionSummaries(summaries: CountrySummary[]): RegionSummary[] {
  const regions = new Map<string, RegionSummary>();
  summaries.forEach((summary) => {
    const region = regionForCountry(summary.country);
    const total = regions.get(region) || {
      region,
      country_count: 0,
      candidate_count: 0,
      map_point_count: 0,
      review_count: 0,
      source_count: 0,
      scenes: {},
    };
    total.country_count += 1;
    total.candidate_count += summary.candidate_count;
    total.map_point_count += summary.map_point_count;
    total.review_count += summary.review_count;
    total.source_count += summary.source_count;
    Object.entries(summary.scenes).forEach(([scene, count]) => {
      total.scenes[scene] = (total.scenes[scene] || 0) + count;
    });
    regions.set(region, total);
  });
  return Array.from(regions.values()).sort(
    (left, right) =>
      Number(left.region === "Other regions") - Number(right.region === "Other regions")
      || right.candidate_count - left.candidate_count
      || regionSortIndex(left.region) - regionSortIndex(right.region)
      || left.region.localeCompare(right.region),
  );
}

function regionForCountry(country: string): string {
  return REGION_BY_COUNTRY.get(normalizeCountryName(country)) || "Other regions";
}

function regionDisplayLabel(region: string): string {
  return REGION_DISPLAY_LABELS[region] || region;
}

function polygonCountryNameFor(country: string): string {
  return COUNTRY_POLYGON_ALIAS_BY_COUNTRY.get(normalizeCountryName(country)) || country;
}

function countryPolygonKey(country: string): string {
  return normalizeCountryName(polygonCountryNameFor(country));
}

function countriesEquivalentForPolygon(left: string, right: string): boolean {
  if (!left || !right) {
    return false;
  }
  return countryPolygonKey(left) === countryPolygonKey(right);
}

function countrySetHasEquivalent(countries: Set<string>, country: string): boolean {
  for (const item of countries) {
    if (countriesEquivalentForPolygon(item, country)) {
      return true;
    }
  }
  return false;
}

function dataCountryNameForPolygon(polygonCountry: string, countries: Set<string>): string {
  for (const item of countries) {
    if (countriesEquivalentForPolygon(item, polygonCountry)) {
      return item;
    }
  }
  return polygonCountry;
}

function normalizeCountryName(country: string): string {
  return country
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/&/g, "and")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function regionSortIndex(region: string): number {
  const index = TARGET_REGION_ORDER.indexOf(region as TargetRegion);
  return index === -1 ? TARGET_REGION_ORDER.length : index;
}

function countryFocus(
  country: string,
  features: MapFeature[],
  polygons: CountryFeature[],
): { lat: number; lng: number } {
  const countryFeatures = features.filter((item) => item.properties.country === country);
  if (countryFeatures.length > 0) {
    return propertyFocus(countryFeatures);
  }
  const position = countryDisplayPosition(country, polygons);
  return { lat: position.lat, lng: position.lng };
}

function countryDisplayPosition(
  country: string,
  polygons: CountryFeature[],
): { lat: number; lng: number; positionSource: CountryPositionSource } {
  return countryDisplayPositionFromPolygon(
    country,
    polygons.find((item) => countriesEquivalentForPolygon(countryName(item), country)),
  );
}

function buildCountryDisplayPositionCache(
  polygons: CountryFeature[],
): Map<string, { lat: number; lng: number; positionSource: CountryPositionSource }> {
  const cache = new Map<string, { lat: number; lng: number; positionSource: CountryPositionSource }>();
  polygons.forEach((polygon) => {
    const name = countryName(polygon);
    if (name) {
      const position = countryDisplayPositionFromPolygon(name, polygon);
      cache.set(name, position);
      cache.set(normalizeCountryName(name), position);
    }
  });
  COUNTRY_POLYGON_ALIASES.forEach(({ country, polygonCountry }) => {
    const polygon = polygons.find((item) => countriesEquivalentForPolygon(countryName(item), polygonCountry));
    if (polygon) {
      const position = countryDisplayPositionFromPolygon(country, polygon);
      cache.set(country, position);
      cache.set(normalizeCountryName(country), position);
    }
  });
  return cache;
}

function countryDisplayPositionFromPolygon(
  country: string,
  polygon: CountryFeature | undefined,
): { lat: number; lng: number; positionSource: CountryPositionSource } {
  const anchor = COUNTRY_DISPLAY_ANCHORS[normalizeCountryName(country)];
  if (anchor) {
    return { ...anchor, positionSource: "display_anchor" };
  }
  if (!polygon) {
    return defaultCountryDisplayPosition(country);
  }
  const points = polygonPoints(polygon.geometry);
  if (points.length === 0) {
    return defaultCountryDisplayPosition(country);
  }
  const totals = points.reduce(
    (sum, [lng, lat]) => ({ lng: sum.lng + lng, lat: sum.lat + lat }),
    { lng: 0, lat: 0 },
  );
  return {
    lng: totals.lng / points.length,
    lat: totals.lat / points.length,
    positionSource: "polygon_centroid",
  };
}

function defaultCountryDisplayPosition(
  country: string,
): { lat: number; lng: number; positionSource: CountryPositionSource } {
  const anchor = COUNTRY_DISPLAY_ANCHORS[normalizeCountryName(country)];
  return anchor
    ? { ...anchor, positionSource: "display_anchor" }
    : { lat: 20, lng: 12, positionSource: "default_fallback" };
}

function propertyFocus(features: MapFeature[]): { lat: number; lng: number } {
  if (features.length === 0) {
    return { lat: 28, lng: 15 };
  }
  const totals = features.reduce(
    (sum, item) => ({
      lng: sum.lng + item.geometry.coordinates[0],
      lat: sum.lat + item.geometry.coordinates[1],
    }),
    { lng: 0, lat: 0 },
  );
  return { lng: totals.lng / features.length, lat: totals.lat / features.length };
}

function polygonPoints(geometry: Geometry): [number, number][] {
  if (geometry.type === "Polygon") {
    return geometry.coordinates.flat() as [number, number][];
  }
  if (geometry.type === "MultiPolygon") {
    return geometry.coordinates.flat(2) as [number, number][];
  }
  return [];
}

function addParam(params: URLSearchParams, key: string, value: string) {
  if (value) {
    params.set(key, value);
  }
}

function responseFilename(contentDisposition: string | null, fallback: string): string {
  if (!contentDisposition) {
    return fallback;
  }
  const encoded = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded.replaceAll('"', ""));
    } catch {
      return fallback;
    }
  }
  const plain = contentDisposition.match(/filename="?([^";]+)"?/i)?.[1];
  return plain?.trim() || fallback;
}

function addPayloadParam(payload: Record<string, string | boolean>, key: string, value: string) {
  if (value) {
    payload[key] = value;
  }
}

function withQuery(path: string, query: string): string {
  return query ? `${path}?${query}` : path;
}

function sceneLabel(scene: string, localization?: LocalizationPayload): string {
  const localized = localization?.labels?.scenes?.[scene]
    || localization?.fallback_labels?.scenes?.[scene];
  return localized || SCENE_LABELS[scene] || scene.replaceAll("_", " ");
}

function enumLabel(value: string | null | undefined, localization?: LocalizationPayload): string {
  if (!value) {
    return uiLabel(localization, "fallback.unknown", "Unknown");
  }
  return localization?.labels?.enums?.[value]
    || localization?.fallback_labels?.enums?.[value]
    || value;
}

function uiLabel(
  localization: LocalizationPayload | undefined,
  key: string,
  fallback: string,
  replacements?: Record<string, string | number>,
): string {
  const value = lookupLocalization(localization?.labels, key)
    ?? lookupLocalization(localization?.fallback_labels, key)
    ?? lookupLocalization(DEFAULT_LOCALIZATION.labels, key)
    ?? fallback;
  return formatTemplate(String(value), replacements);
}

function lookupLocalization(labels: Record<string, any> | undefined, key: string): unknown {
  let current: unknown = labels;
  for (const part of key.split(".")) {
    if (!current || typeof current !== "object" || !(part in current)) {
      return undefined;
    }
    current = (current as Record<string, unknown>)[part];
  }
  return current;
}

function formatTemplate(value: string, replacements?: Record<string, string | number>): string {
  if (!replacements) {
    return value;
  }
  return Object.entries(replacements).reduce(
    (text, [key, replacement]) => text.replaceAll(`{${key}}`, String(replacement)),
    value,
  );
}

function taskBacklogTotal(backlog: Record<string, number>): number {
  return Object.values(backlog).reduce((total, count) => total + count, 0);
}

function formatNumber(value: number | null | undefined, locale = "en"): string {
  if (value === null || value === undefined) {
    return "Unknown";
  }
  return new Intl.NumberFormat(locale === "zh" ? "zh-CN" : "en", { maximumFractionDigits: 1 }).format(value);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Request failed";
}

declare global {
  interface Window {
    __isite2SelectCountry?: (country: string) => void;
    __isite2GlobeControlState?: () => GlobeControlState | null;
    __isite2SetPointOfView?: (lat: number, lng: number, altitude?: number) => void;
    __isite2GlobeAssetState?: () => {
      globeImageUrl: string;
      bumpImageUrl: string;
    };
    __isite2CountryVisualState?: () => {
      boundaryPathCount: number;
      beaconRingCount: number;
      beaconMotionEnabled: boolean;
      beaconRepeatPeriods: number[];
      dataCountryCapColor: string;
      dataCountryStrokeColor: string;
      pathDashAnimateTime: number;
    };
  }
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
