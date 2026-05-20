import { expect, test, type Page } from "@playwright/test";

const RUN_ID = "mock-run-001";
const SECOND_RUN_ID = "mock-run-002";
const SCENES = ["airport_terminal", "convention_center", "stadium", "mall_mixed_use"];
const COUNTRIES = [
  { country: "Algeria" },
  { country: "Egypt" },
];
const PLACES = [
  { country: "Algeria", city: "Algiers", lat: 36.691, lng: 3.215 },
  { country: "Algeria", city: "Oran", lat: 35.697, lng: -0.633 },
  { country: "Egypt", city: "Cairo", lat: 30.112, lng: 31.4 },
];
const APAC_PLACES = [
  { country: "Sri Lanka", city: "Colombo", lat: 6.9271, lng: 79.8612 },
  { country: "Cambodia", city: "Phnom Penh", lat: 11.5564, lng: 104.9282 },
  { country: "Maldives", city: "Male", lat: 4.1755, lng: 73.5093 },
];
const CITY_ONLY_PLACE = { country: "Algeria", city: "Setif", lat: 36.1911, lng: 5.4137 };
const DENSE_ALGERIA_PLACES = [
  { country: "Algeria", city: "Bab Ezzouar", lat: 36.7147, lng: 3.1838 },
  { country: "Algeria", city: "Ben Aknoun", lat: 36.7582, lng: 3.0135 },
  { country: "Algeria", city: "Bir Mourad Rais", lat: 36.7351, lng: 3.0506 },
  { country: "Algeria", city: "Dar El Beida", lat: 36.7133, lng: 3.2125 },
  { country: "Algeria", city: "El Harrach", lat: 36.7161, lng: 3.1366 },
  { country: "Algeria", city: "El Madania", lat: 36.7473, lng: 3.0702 },
  { country: "Algeria", city: "Hydra", lat: 36.7476, lng: 3.0404 },
  { country: "Algeria", city: "Hussein Dey", lat: 36.7452, lng: 3.0982 },
  { country: "Algeria", city: "Kouba", lat: 36.7275, lng: 3.0851 },
  { country: "Algeria", city: "Mohammadia", lat: 36.7357, lng: 3.1469 },
];
const EMPTY_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lS8tJAAAAABJRU5ErkJggg==",
  "base64",
);
const PACKETS = COUNTRIES.flatMap((country) =>
  PLACES.filter((place) => place.country === country.country).flatMap((place) =>
    SCENES.map((scene, index) =>
      createPacket(place, scene, index, {
        coordinateStatus:
          place.country === "Algeria" && place.city === "Algiers" && scene === "stadium"
            ? "Review Required"
            : "Map Ready",
      }),
    ),
  ),
).concat(createPacket(CITY_ONLY_PLACE, "airport_terminal", 0, { coordinateStatus: "Review Required" }));
const DENSE_PACKETS = PACKETS.concat(
  DENSE_ALGERIA_PLACES.map((place, index) => createPacket(place, SCENES[index % SCENES.length], 0)),
);
const APAC_PACKETS = APAC_PLACES.map((place, index) =>
  createPacket(place, SCENES[index % SCENES.length], 0),
);

test("renders the opportunity globe as the default product surface", async ({ page }) => {
  const dataRequests: string[] = [];
  const scanRunRequests: string[] = [];
  await installMockApi(page, { dataRequests, scanRunRequests });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".globe-stage")).toBeVisible();
  await expect(page.locator(".agent-workbench-stage")).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Agent workbench view" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Globe view" })).toHaveCount(0);
  await expectGlobeMarkerCount(page, 2);
  await expect(page.locator(".insight-panel")).toBeVisible();
  expect(scanRunRequests).toEqual([]);
});

test("public view hides write and connector entrypoints", async ({ page }) => {
  const dataRequests: string[] = [];
  const geocodeRequests: string[] = [];
  const exportRequests: Array<{ path: string; body: Record<string, unknown> }> = [];
  const ragRequests: Array<{ path: string; body: Record<string, unknown> }> = [];
  await installMockApi(page, {
    dataRequests,
    geocodeRequests,
    exportRequests,
    ragRequests,
    runtimeConfig: {
      mode: "public_view",
      features: {
        exports: false,
        rag: false,
        connectors: false,
        geocode: false,
      },
    },
  });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("button", { name: "Ask iSite2" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Export Excel" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Export PPT" })).toHaveCount(0);
  await page.getByRole("button", { name: /Algeria, 9 candidate properties/ }).click();
  await expect(page.locator(".panel-head h2")).toContainText("Algeria", { timeout: 10_000 });
  await expect.poll(() => geocodeRequests.length).toBe(0);
  expect(exportRequests).toEqual([]);
  expect(ragRequests).toEqual([]);
});

test("groups newly scanned APAC countries in the overview region distribution", async ({ page }) => {
  const dataRequests: string[] = [];
  const exportRequests: Array<{ path: string; body: Record<string, unknown> }> = [];
  await installMockApi(page, {
    dataRequests,
    exportRequests,
    packets: APAC_PACKETS,
  });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".globe-stage")).toBeVisible();
  await expect(page.getByText("Region distribution", { exact: true })).toBeVisible();
  await expect(page.getByText("Asia Pacific", { exact: true })).toBeVisible();
  await expect(page.getByText("Other regions", { exact: true })).toHaveCount(0);
  await expect(page.locator(".region-row").filter({ hasText: "Asia Pacific" })).toContainText(
    "3 countries",
  );
  const maldivesMarker = page.getByRole("button", {
    name: /Maldives, 1 candidate properties, country display marker/,
  });
  await expect(maldivesMarker).toBeVisible();
  await expect(maldivesMarker).toHaveAttribute("title", /country display anchor/);
  await expect(page.getByRole("button", { name: /Sri Lanka, 1 candidate properties/ }))
    .toHaveAttribute("title", /0 mapped cities/);
  await expect(page.getByRole("button", { name: /Cambodia, 1 candidate properties/ }))
    .toHaveAttribute("title", /0 mapped cities/);

  await maldivesMarker.click();
  await expect(page.locator(".panel-head h2")).toContainText("Maldives", { timeout: 10_000 });
  await expectPropertyCardCount(page, 1);
  await page.getByRole("button", { name: "Export Excel" }).click();
  await expect.poll(() => exportRequests.length).toBe(1);
  expect(exportRequests[0]).toEqual({
    path: "/outputs/excel",
    body: { country: "Maldives" },
  });
});

test("filters property cards by the selected scene primary metric threshold", async ({ page }) => {
  const dataRequests: string[] = [];
  const metricPackets = [
    withPrimaryEvidence(
      createPacket(PLACES[0], "luxury_hotel_mice", 0),
      "room_count",
      "40 rooms",
    ),
    withPrimaryEvidence(
      createPacket(PLACES[1], "luxury_hotel_mice", 1),
      "room_count",
      "75 rooms",
    ),
    withPrimaryEvidence(
      createPacket(CITY_ONLY_PLACE, "stadium", 0),
      "seat_count",
      "18,000 seats",
    ),
  ];
  await installMockApi(page, { dataRequests, packets: metricPackets });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("button", { name: /Algeria, 3 candidate properties/ })).toBeVisible();
  await page.getByRole("button", { name: /Algeria, 3 candidate properties/ }).click();
  await expect(page.locator(".panel-head h2")).toContainText("Algeria", { timeout: 10_000 });
  await expectPropertyCardCount(page, 3);

  await page.locator(".filter-strip select").first().selectOption("luxury_hotel_mice");
  await expectPropertyCardCount(page, 2);
  await expect(page.getByLabel("Rooms minimum")).toHaveValue("0");

  await page.getByLabel("Rooms minimum").fill("50");
  await expectPropertyCardCount(page, 1);
  await expect(page.locator(".property-card")).toContainText("Oran luxury hotel mice");
  await expect(page.locator(".property-card")).not.toContainText("Algiers luxury hotel mice");
});

test("shows airport objective primary metric instead of gateway role on property cards", async ({ page }) => {
  const airportPacket = withEvidence(
    createPacket(PLACES[0], "airport_terminal", 0),
    [
      {
        field_group: "airport_role",
        indicator_name: "gateway_role",
        field_value: "Primary international gateway airport for Algiers.",
      },
      {
        field_group: "annual_passenger_throughput",
        indicator_name: "annual_passenger_throughput",
        field_value: "2024 passenger throughput: 10,000,000 passengers",
      },
    ],
  );
  await installMockApi(page, { dataRequests: [], packets: [airportPacket] });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /Algeria, 1 candidate properties/ }).click();
  await expectPropertyCardCount(page, 1);
  await expect(page.locator(".property-card")).toContainText("2024 passenger throughput: 10,000,000 passengers");
  await expect(page.locator(".property-card")).not.toContainText("Primary international gateway airport");
});

test("filters the right panel from scene distribution and sorts by primary metric", async ({ page }) => {
  const dataRequests: string[] = [];
  const panelPackets = [
    withPrimaryEvidence(
      createPacket(PLACES[0], "luxury_hotel_mice", 0),
      "room_count",
      "60 rooms",
    ),
    withPrimaryEvidence(
      createPacket(PLACES[1], "luxury_hotel_mice", 1),
      "room_count",
      "140 rooms",
    ),
    withPrimaryEvidence(
      createPacket(CITY_ONLY_PLACE, "luxury_hotel_mice", 2),
      "room_count",
      "90 rooms",
    ),
    withPrimaryEvidence(
      createPacket(PLACES[0], "stadium", 0),
      "seat_count",
      "18,000 seats",
    ),
  ];
  await installMockApi(page, { dataRequests, packets: panelPackets });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /Algeria, 4 candidate properties/ }).click();
  await expect(page.locator(".panel-head h2")).toContainText("Algeria", { timeout: 10_000 });
  await expectPropertyCardCount(page, 4);
  await expectGlobeMarkerCount(page, 3);
  await expect(page.locator(".filter-strip select").first()).toHaveValue("");
  const requestCountAfterLoad = dataRequests.length;

  const sceneDistribution = page.getByLabel("Scene distribution");
  const hotelScene = sceneDistribution.getByRole("button", { name: /Hotel \/ MICE/ });
  await hotelScene.click();

  await expect(hotelScene).toHaveAttribute("aria-pressed", "true");
  await expectPropertyCardCount(page, 3);
  await expect(page.locator(".filter-strip select").first()).toHaveValue("");
  await expectGlobeMarkerCount(page, 3);
  await expect.poll(async () => propertyCardTitles(page)).toEqual([
    "Oran luxury hotel mice",
    "Setif luxury hotel mice",
    "Algiers luxury hotel mice",
  ]);
  await expect(page.locator(".property-list")).not.toContainText("Algiers stadium");
  await page.waitForTimeout(250);
  expect(dataRequests.length).toBe(requestCountAfterLoad);

  await hotelScene.click();
  await expect(hotelScene).toHaveAttribute("aria-pressed", "false");
  await expectPropertyCardCount(page, 4);
});

test("requests the global candidate pool for the opportunity globe", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  const dataRequests: string[] = [];
  const geocodeRequests: string[] = [];
  const scanRunRequests: string[] = [];
  const exportRequests: Array<{ path: string; body: Record<string, unknown> }> = [];
  const ragRequests: Array<{ path: string; body: Record<string, unknown> }> = [];
  await installMockApi(page, {
    dataRequests,
    geocodeRequests,
    scanRunRequests,
    exportRequests,
    ragRequests,
  });

  await page.goto("/ui/?mock_globe=1&view=globe", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".globe-stage")).toBeVisible();
  await expect(page.getByText("Scan", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Export Run", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Run scan" })).toHaveCount(0);
  await expectDataRequestCount(dataRequests, 1);
  expect(scanRunRequests).toEqual([]);
  await expectPropertyCardCount(page, 0);
  await expectGlobeMarkerCount(page, 2);
  await expectKpiLabels(page, ["Countries", "Candidates", "Sources"]);
  await expect(page.getByText("Region distribution", { exact: true })).toBeVisible();
  await expect(page.getByText("Africa", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /Algeria, 9 candidate properties/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Egypt, 4 candidate properties/ })).toBeVisible();
  expect(geocodeRequests).toEqual([]);
  await expect(page.locator(".country-rail")).toHaveCount(0);
  await expectGlobeMode(page, "overview");

  await page.getByRole("button", { name: /Algeria, 9 candidate properties/ }).click();
  await expect(page.locator(".panel-head h2")).toContainText("Algeria", { timeout: 10_000 });
  await expectPropertyCardCount(page, 9);
  await expectGlobeMarkerCount(page, 3);
  await expectKpiLabels(page, ["Cities", "Candidates", "Sources"]);
  const mediaBox = await firstPropertyMediaBox(page);
  expect(mediaBox?.width ?? 0).toBeGreaterThan(80);
  expect(mediaBox?.height ?? 0).toBeGreaterThan(100);
  await page.getByRole("button", { name: "Export Excel" }).click();
  await expect.poll(() => exportRequests.length).toBe(1);
  expect(exportRequests[0]).toEqual({
    path: "/outputs/excel",
    body: { country: "Algeria" },
  });
  expect(geocodeRequests).toEqual([]);
  await expectGlobeMode(page, "focused");

  await expect(page.getByRole("button", { name: /Algiers, Algeria, 4 candidate properties/ }))
    .toHaveAttribute("title", /3 mapped/);
  await expect(page.getByRole("button", { name: /Setif, Algeria, 1 candidate properties/ }))
    .toHaveAttribute("title", /estimated city position/);

  await page.getByRole("button", { name: /Algiers, Algeria/ }).click();
  await expect(page.locator(".panel-head h2")).toContainText("Algiers", { timeout: 10_000 });
  await expectPropertyCardCount(page, 4);
  await expectGlobeMarkerCount(page, 3);
  await expectGlobeMode(page, "focused");

  await clickFirstPropertyCard(page);
  await expect(page.locator(".dossier")).toBeVisible();
  await expect(page.locator(".street-map")).toBeVisible();
  await expectPropertyCardCount(page, 0);
  await expectGlobeMode(page, "focused");

  await page.getByRole("button", { name: "Ask iSite2" }).click();
  const ragDialog = page.getByRole("dialog", { name: "Ask iSite2 RAG assistant" });
  await expect(ragDialog).toBeVisible();
  await expect(ragDialog).toContainText("Algiers airport terminal");
  await expect(ragDialog).toContainText("Evidence-backed retrieval ready");
  await ragDialog.getByRole("textbox", { name: "Question" }).fill(
    "Why is this property recommended?",
  );
  await ragDialog.getByRole("button", { name: "Send" }).click();
  await expect(ragDialog).toContainText("Mock evidence-backed answer");
  await expect(ragDialog).toContainText("Mock Airport Authority");
  expect(ragRequests.every((request) => !("scan_run_id" in request.body))).toBe(true);
  await ragDialog.getByRole("button", { name: "Close Ask iSite2" }).click();

  await page.getByRole("button", { name: "Back to list" }).click();
  await expect(page.locator(".panel-head h2")).toContainText("Algiers", { timeout: 10_000 });
  await expectPropertyCardCount(page, 4);

  await page.getByRole("button", { name: "All cities in Algeria" }).click();
  await expect(page.locator(".panel-head h2")).toContainText("Algeria", { timeout: 10_000 });
  await expectPropertyCardCount(page, 9);
  await expectGlobeMarkerCount(page, 3);

  await page.getByRole("button", { name: /Setif, Algeria/ }).click();
  await expect(page.locator(".panel-head h2")).toContainText("Setif", { timeout: 10_000 });
  await expectPropertyCardCount(page, 1);
  await page.getByRole("button", { name: "Export PPT" }).click();
  await expect.poll(() => exportRequests.length).toBe(2);
  expect(exportRequests[1]).toEqual({
    path: "/outputs/ppt",
    body: { country: "Algeria", city: "Setif" },
  });

  await page.getByRole("button", { name: /Country scope: Algeria/ }).click();
  await page.evaluate(() => {
    const appWindow = window as typeof window & {
      __isite2SelectCountry?: (country: string) => void;
    };
    appWindow.__isite2SelectCountry?.("");
  });
  await expect(page.locator(".panel-head h2")).toContainText("All candidate countries", { timeout: 10_000 });
  await expectPropertyCardCount(page, 0);
  await expectGlobeMarkerCount(page, 2);
  await expectKpiLabels(page, ["Countries", "Candidates", "Sources"]);
  await expectGlobeMode(page, "overview");

  expectDataRequestsUseGlobalCandidatePool(dataRequests);
  expect(scanRunRequests).toEqual([]);

  expect(consoleErrors.filter((line) => !isIgnorableConsoleError(line))).toEqual([]);
});

test("renders real country overlay markers in overview mode", async ({ page }) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const dataRequests: string[] = [];
  const scanRunRequests: string[] = [];
  await installMockApi(page, { dataRequests, scanRunRequests });

  await page.goto("/ui/?view=globe", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".marker-overlay-layer")).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".marker-overlay-layer .country-marker")).toHaveCount(2, { timeout: 60_000 });
  await expectGlobeMode(page, "overview");
  expect(scanRunRequests).toEqual([]);

  expect(pageErrors).toEqual([]);
  expect(consoleErrors.filter((line) => !isIgnorableConsoleError(line))).toEqual([]);
});

test("hides globe markers after they rotate behind the earth", async ({ page }) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await installMockApi(page, { dataRequests: [], scanRunRequests: [] });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1&test_pov=0,-160", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".marker-overlay-layer")).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".marker-overlay-layer .country-marker")).toHaveCount(2, { timeout: 60_000 });

  await waitForHiddenMarkerLabels(page, [
    "Algeria, 9 candidate properties",
    "Egypt, 4 candidate properties",
  ]);

  expect(pageErrors).toEqual([]);
  expect(consoleErrors.filter((line) => !isIgnorableConsoleError(line))).toEqual([]);
});

test("clusters dense country city markers and spider-expands them", async ({ page }) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const dataRequests: string[] = [];
  const exportRequests: Array<{ path: string; body: Record<string, unknown> }> = [];
  await installMockApi(page, {
    dataRequests,
    exportRequests,
    packets: DENSE_PACKETS,
  });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".marker-overlay-layer")).toBeVisible({ timeout: 60_000 });
  await expect
    .poll(() => page.evaluate(() => {
      const appWindow = window as typeof window & {
        __isite2SelectCountry?: (country: string) => void;
      };
      return Boolean(appWindow.__isite2SelectCountry);
    }), { timeout: 60_000 })
    .toBe(true);
  const selectedCountry = await page.evaluate(() => {
    const appWindow = window as typeof window & {
      __isite2SelectCountry?: (country: string) => void;
    };
    appWindow.__isite2SelectCountry?.("Algeria");
    return Boolean(appWindow.__isite2SelectCountry);
  });
  expect(selectedCountry).toBe(true);
  await expect(page.locator(".panel-head h2")).toContainText("Algeria", { timeout: 20_000 });

  const clusterButton = page.getByRole("button", { name: /clustered cities in Algeria/i }).first();
  await expect(clusterButton).toBeVisible({ timeout: 60_000 });
  await expect
    .poll(() => visibleRealClusterMarkerCount(page), { timeout: 20_000 })
    .toBeGreaterThan(0);
  await expect(
    page.locator(".marker-overlay-layer").getByRole("button", { name: /Bir Mourad Rais, Algeria/ }),
  ).toHaveCount(0);

  const expandedCluster = await page.evaluate(() => {
    const cluster = document.querySelector<HTMLButtonElement>(".marker-overlay-layer .cluster-marker");
    if (cluster) {
      window.setTimeout(() => cluster.click(), 0);
    }
    return Boolean(cluster);
  });
  expect(expandedCluster).toBe(true);
  await expect
    .poll(() => visibleSpiderChildCount(page), { timeout: 20_000 })
    .toBeGreaterThan(1);
  const selectedCity = await page.evaluate(() => {
    const city = Array.from(document.querySelectorAll<HTMLButtonElement>(".marker-overlay-layer .spider-child"))
      .find((button) => button.getAttribute("aria-label")?.includes("Bir Mourad Rais, Algeria"));
    if (city) {
      window.setTimeout(() => city.click(), 0);
    }
    return Boolean(city);
  });
  expect(selectedCity).toBe(true);
  await expect(page.locator(".panel-head h2")).toContainText("Bir Mourad Rais", { timeout: 20_000 });

  await page.getByRole("button", { name: "Export Excel" }).click();
  await expect.poll(() => exportRequests.length).toBe(1);
  expect(exportRequests[0]).toEqual({
    path: "/outputs/excel",
    body: { country: "Algeria", city: "Bir Mourad Rais" },
  });

  expect(pageErrors).toEqual([]);
  expect(consoleErrors.filter((line) => !isIgnorableConsoleError(line))).toEqual([]);
});

async function expectDataRequestCount(dataRequests: string[], expectedMinimum: number) {
  await expect
    .poll(() => dataRequests.length, { timeout: 30_000 })
    .toBeGreaterThanOrEqual(expectedMinimum);
}

async function expectPropertyCardCount(page: Page, expected: number, timeout = 10_000) {
  await expect
    .poll(() => page.evaluate(() => document.querySelectorAll(".property-card").length), {
      timeout,
    })
    .toBe(expected);
}

function propertyCardTitles(page: Page) {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll(".property-card h3"))
      .map((element) => element.textContent?.trim() || ""),
  );
}

async function expectGlobeMarkerCount(page: Page, expected: number, timeout = 10_000) {
  await expect
    .poll(() => page.evaluate(() => document.querySelectorAll(".city-marker").length), {
      timeout,
    })
    .toBe(expected);
}

async function expectKpiLabels(page: Page, expected: string[], timeout = 10_000) {
  await expect
    .poll(() => page.evaluate(() =>
      Array.from(document.querySelectorAll(".panel-head .kpi span"))
        .map((element) => element.textContent?.trim() || ""),
    ), { timeout })
    .toEqual(expected);
}

function visibleRealClusterMarkerCount(page: Page) {
  return page.evaluate(() => {
    return Array.from(document.querySelectorAll<HTMLElement>(".marker-overlay-layer .cluster-marker"))
      .filter((element) => {
        const style = window.getComputedStyle(element);
        return style.visibility !== "hidden" && style.pointerEvents !== "none";
      })
      .length;
  });
}

function visibleRealCountryMarkerCount(page: Page) {
  return page.evaluate(() => {
    return Array.from(document.querySelectorAll<HTMLElement>(".marker-overlay-layer .country-marker"))
      .filter((element) => {
        const style = window.getComputedStyle(element);
        return style.visibility !== "hidden" && style.pointerEvents !== "none";
      })
      .length;
  });
}

function visibleSpiderChildCount(page: Page) {
  return page.evaluate(() => {
    return Array.from(document.querySelectorAll<HTMLElement>(".marker-overlay-layer .spider-child"))
      .filter((element) => {
        const style = window.getComputedStyle(element);
        return style.visibility !== "hidden" && style.pointerEvents !== "none";
      })
      .length;
  });
}

async function waitForVisibleMarkerLabels(page: Page, labels: string[], timeout = 20_000) {
  await page.waitForFunction((expectedLabels) => {
    return expectedLabels.every((label) => {
      const target = Array.from(document.querySelectorAll<HTMLElement>(".marker-overlay-layer .country-marker"))
        .find((element) => element.getAttribute("aria-label") === label);
      if (!target) {
        return false;
      }
      const style = window.getComputedStyle(target);
      return style.visibility !== "hidden" && style.pointerEvents !== "none";
    });
  }, labels, { timeout });
}

async function waitForHiddenMarkerLabels(page: Page, labels: string[], timeout = 20_000) {
  await page.waitForFunction((expectedLabels) => {
    return expectedLabels.every((label) => {
      const target = Array.from(document.querySelectorAll<HTMLElement>(".marker-overlay-layer .country-marker"))
        .find((element) => element.getAttribute("aria-label") === label);
      if (!target) {
        return true;
      }
      const style = window.getComputedStyle(target);
      return style.visibility === "hidden" && style.pointerEvents === "none";
    });
  }, labels, { timeout });
}

function firstPropertyMediaBox(page: Page) {
  return page.evaluate(() => {
    const element = document.querySelector<HTMLElement>(".property-card img, .hero-fallback");
    if (!element) {
      return null;
    }
    const rect = element.getBoundingClientRect();
    return { width: rect.width, height: rect.height };
  });
}

async function clickFirstPropertyCard(page: Page) {
  const clicked = await page.evaluate(() => {
    const button = document.querySelector<HTMLButtonElement>(".property-card button");
    button?.click();
    return Boolean(button);
  });
  expect(clicked).toBe(true);
}

async function expectGlobeMode(page: Page, mode: "overview" | "focused") {
  await expect(page.locator(".globe-stage")).toHaveAttribute("data-globe-mode", mode, {
    timeout: 20_000,
  });
}

type MockApiOptions = {
  dataRequests: string[];
  geocodeRequests?: string[];
  scanRunRequests?: string[];
  exportRequests?: Array<{ path: string; body: Record<string, unknown> }>;
  ragRequests?: Array<{ path: string; body: Record<string, unknown> }>;
  runtimeConfig?: {
    mode: string;
    features: {
      exports: boolean;
      rag: boolean;
      connectors: boolean;
      geocode: boolean;
    };
  };
  packets?: Array<ReturnType<typeof createPacket>>;
};

async function installMockApi(page: Page, {
  dataRequests,
  geocodeRequests = [],
  scanRunRequests = [],
  exportRequests = [],
  ragRequests = [],
  runtimeConfig = {
    mode: "local",
    features: {
      exports: true,
      rag: true,
      connectors: true,
      geocode: true,
    },
  },
  packets = PACKETS,
}: MockApiOptions) {
  let apiPackets = [...packets];
  await page.route("https://unpkg.com/three-globe/example/img/**", (route) =>
    route.fulfill({ body: EMPTY_PNG, contentType: "image/png" }),
  );
  await page.route("https://www.openstreetmap.org/**", (route) =>
    route.fulfill({
      body: "<!doctype html><html><body><div>Street map</div></body></html>",
      contentType: "text/html",
    }),
  );
  await page.route((url) => url.pathname === "/runtime-config", (route) =>
    route.fulfill({ json: runtimeConfig }),
  );
  await page.route((url) => url.pathname === "/scan-runs", (route) => {
    scanRunRequests.push(route.request().url());
    return route.fulfill({ json: [scanRun(RUN_ID, apiPackets.length), scanRun(SECOND_RUN_ID, apiPackets.length)] });
  });
  await page.route((url) => url.pathname === "/discovery/status", (route) =>
    route.fulfill({
      json: {
        task_backlog: { queued: 6, leased: 1, completed: 8 },
        progress_status: { active: 3, blocked_review: 1 },
        progress: discoveryProgressRows(apiPackets),
        pending_evidence_count: 2,
        raw_evidence_status: { new: 2, accepted: 6, review: 1 },
        latest_run: {
          id: "mock-discovery-run",
          worker_id: "firecrawl-worker-1",
          status: "completed",
          started_at: "2026-05-09T09:00:00Z",
          completed_at: "2026-05-09T09:05:00Z",
          searched_count: 12,
          fetched_count: 8,
          discovered_count: 7,
          new_count: 3,
          changed_count: 1,
          duplicate_count: 2,
          failed_count: 0,
          countries: COUNTRIES.map((row) => row.country),
          errors: [],
        },
      },
    }),
  );
  await page.route((url) => url.pathname === "/raw-evidence", (route) => {
    const packets = filteredPackets(route.request().url(), apiPackets);
    return route.fulfill({
      json: {
        count: packets.length,
        items: packets.map(rawEvidenceRow),
      },
    });
  });
  await page.route((url) => url.pathname === "/candidate-drafts", (route) => {
    const packets = filteredPackets(route.request().url(), apiPackets);
    return route.fulfill({
      json: {
        count: packets.length,
        items: packets.map(candidateDraftRow),
      },
    });
  });
  await page.route((url) => url.pathname === "/review-queue", (route) => {
    const packets = filteredPackets(route.request().url(), apiPackets);
    return route.fulfill({ json: packets.flatMap(reviewQueueRows) });
  });
  await page.route((url) => url.pathname === "/connectors/geocode", (route) => {
    geocodeRequests.push(route.request().url());
    const query = new URL(route.request().url()).searchParams.get("q");
    if (query !== "Setif, Algeria") {
      return route.fulfill({ json: { detail: "geocode result not found" }, status: 404 });
    }
    return route.fulfill({
      json: {
        latitude: CITY_ONLY_PLACE.lat,
        longitude: CITY_ONLY_PLACE.lng,
        geocode_precision: "city centroid",
        map_source: "Mock city centroid",
        city: CITY_ONLY_PLACE.city,
        country: CITY_ONLY_PLACE.country,
        display_name: "Setif, Algeria",
        boundingbox: null,
      },
    });
  });
  await page.route((url) => url.pathname === "/map/country-summary", (route) => {
    dataRequests.push(route.request().url());
    return route.fulfill({ json: countrySummaries(apiPackets) });
  });
  await page.route((url) => url.pathname === "/map/city-summary", (route) => {
    dataRequests.push(route.request().url());
    const packets = filteredPackets(route.request().url(), apiPackets);
    return route.fulfill({ json: { cities: citySummaries(packets) } });
  });
  await page.route((url) => url.pathname === "/map/properties", (route) => {
    dataRequests.push(route.request().url());
    const packets = filteredPackets(route.request().url(), apiPackets);
    return route.fulfill({
      json: {
        type: "FeatureCollection",
        features: packets.filter(isMapReadyPacket).map(mapFeature),
      },
    });
  });
  await page.route((url) => url.pathname === "/properties", (route) => {
    dataRequests.push(route.request().url());
    const packets = filteredPackets(route.request().url(), apiPackets);
    return route.fulfill({
      json: {
        candidate_count: packets.length,
        display_count: packets.length,
        packets,
      },
    });
  });
  await page.route((url) => url.pathname === "/outputs/excel" || url.pathname === "/outputs/ppt", async (route) => {
    exportRequests.push({
      path: new URL(route.request().url()).pathname,
      body: JSON.parse(route.request().postData() || "{}") as Record<string, unknown>,
    });
    return route.fulfill({
      status: 202,
      json: {
        artifact_type: new URL(route.request().url()).pathname.endsWith("/excel") ? "excel" : "ppt",
        path: "/tmp/mock-output",
        candidate_count: 1,
        filter_snapshot: {},
      },
    });
  });
  await page.route((url) => url.pathname === "/rag/index", (route) =>
    {
      ragRequests.push({
        path: new URL(route.request().url()).pathname,
        body: JSON.parse(route.request().postData() || "{}") as Record<string, unknown>,
      });
      return route.fulfill({
      status: 202,
      json: { indexed_documents: 1, indexed_chunks: 1, skipped_documents: 0 },
      });
    },
  );
  await page.route((url) => url.pathname === "/rag/query", (route) =>
    {
      ragRequests.push({
        path: new URL(route.request().url()).pathname,
        body: JSON.parse(route.request().postData() || "{}") as Record<string, unknown>,
      });
      return route.fulfill({
      json: {
        answer: "Mock evidence-backed answer for this property.",
        citations: [
          {
            chunk_id: "chunk-1",
            source_url: "https://example.com/mock-source",
            source_name: "Mock Airport Authority",
            source_tier: "Tier 1",
            source_date: "2026",
            fetched_at: "2026-05-09T00:00:00Z",
            raw_evidence_id: null,
            property_id: "mock-property-1",
            field_group: "annual_passenger_throughput",
            excerpt: "Mock airport handled 10,000,000 passengers.",
          },
        ],
        priority_recommendations: [
          {
            property_id: "mock-property-1",
            property_name: "Algiers airport terminal",
            country: "Algeria",
            scene_type: "airport_terminal",
            priority_band: "Evidence-backed Priority",
            evidence_status: "Supported",
            action_class: "Survey First",
            recommended_solution: "pRRU",
            rationale: "Mock rationale tied to public evidence.",
            review_next_actions: ["Mock review action"],
          },
        ],
        review_actions: ["Mock review action"],
      },
      });
    },
  );
}

function filteredPackets(url: string, packets: Array<ReturnType<typeof createPacket>>) {
  const params = new URL(url).searchParams;
  const country = params.get("country");
  const scene = params.get("scene_type");
  const evidence = params.get("evidence_status");
  const action = params.get("action_class");
  return packets.filter((packet) => {
    return (
      (!country || packet.entity.country === country) &&
      (!scene || packet.entity.scene_type === scene) &&
      (!evidence || packet.conclusion.evidence_status === evidence) &&
      (!action || packet.conclusion.action_class === action)
    );
  });
}

function scanRun(runId: string, candidateCount = PACKETS.length) {
  return {
    run_id: runId,
    status: "completed",
    created_at: "2026-05-09T09:00:00Z",
    scope: { countries: COUNTRIES.map((row) => row.country) },
    candidate_count: candidateCount,
    review_count: candidateCount,
    storage_mode: "mock",
  };
}

function createPacket(
  place: { country: string; city: string; lat: number; lng: number },
  scene: string,
  index: number,
  options: { coordinateStatus?: string } = {},
) {
  const id = `${place.country.toLowerCase().replaceAll(" ", "-")}-${place.city.toLowerCase().replaceAll(" ", "-")}-${scene}`;
  const primaryEvidence = mockPrimaryEvidence(scene, place, index);
  return {
    entity: {
      property_id: id,
      country: place.country,
      city: place.city,
      property_name: `${place.city} ${scene.replaceAll("_", " ")}`,
      scene_type: scene,
      latitude: place.lat + index * 0.018,
      longitude: place.lng + index * 0.021,
      geocode_precision: "venue centroid",
      map_source: "Mock map source",
      map_source_date: "2026-05-09",
      google_maps_link: `https://www.google.com/maps/search/?api=1&query=${place.lat},${place.lng}`,
      coordinate_status: options.coordinateStatus || "Map Ready",
      hero_image: null,
    },
    scene: {
      annual_visits_est: 1_000_000 + index * 100_000,
      proxy_level: "Supported",
      area_metric_name: "mock metric",
      proxy_basis: "mock evidence basis",
    },
    evidence: [
      {
        field_group: primaryEvidence.indicatorName,
        field_value: primaryEvidence.fieldValue,
        indicator_name: primaryEvidence.indicatorName,
        source_name: "Mock source",
        source_url: "https://example.com/mock-source",
        source_date: "2026-05-09",
        source_tier: "Tier 1",
        evidence_type: "official",
        cross_check_status: "Cross-checked",
      },
    ],
    build_status: {
      indoor_system_presence: "Unknown",
      indoor_system_type: "Unknown",
      indoor_rat: "Unknown",
      build_evidence_status: "Insufficient",
    },
    demand: {
      busy_hour_traffic_gb: 12 + index,
      busy_hour_bandwidth_mbps: 260 + index * 20,
      busy_hour_users: 600 + index * 50,
    },
    inference: [
      {
        inferred_field: "busy hour users",
        inferred_value: `${600 + index * 50}`,
        inference_basis: "mock traffic profile",
        inference_chain: "mock visits -> busy users -> traffic",
        inference_confidence: "Medium",
      },
    ],
    conclusion: {
      evidence_status: "Supported",
      value_class: "City Core",
      action_class: "Survey First",
      recommended_solution: "Survey First",
      reason_to_recommend: "Mock evidence supports a high-value opportunity.",
      next_action: "Validate coordinate and indoor build status.",
    },
    review_queue: [
      {
        reason: "Indoor build status unknown",
        next_action: "Check operator indoor system announcement.",
        status: "open",
      },
    ],
  };
}

function mockPrimaryEvidence(
  scene: string,
  place: { city: string },
  index: number,
) {
  const cityScene = `${place.city} ${scene.replaceAll("_", " ")}`;
  switch (scene) {
    case "airport_terminal":
      return {
        indicatorName: "annual_passenger_throughput",
        fieldValue: `${cityScene} handled ${formatMockMetric(12_000_000 + index * 1_000_000)} passengers`,
      };
    case "convention_center":
      return {
        indicatorName: "exhibition_area",
        fieldValue: `${cityScene} has ${formatMockMetric(25_000 + index * 2_000)} sqm exhibition area`,
      };
    case "stadium":
      return {
        indicatorName: "seat_count",
        fieldValue: `${cityScene} has ${formatMockMetric(40_000 + index * 5_000)} seats`,
      };
    case "mall_mixed_use":
      return {
        indicatorName: "gla",
        fieldValue: `${cityScene} has ${formatMockMetric(80_000 + index * 8_000)} sqm GLA`,
      };
    case "luxury_hotel_mice":
      return {
        indicatorName: "room_count",
        fieldValue: `${cityScene} has ${formatMockMetric(120 + index * 20)} rooms`,
      };
    case "office_government":
      return {
        indicatorName: "office_gfa",
        fieldValue: `${cityScene} has ${formatMockMetric(60_000 + index * 6_000)} sqm office GFA`,
      };
    case "hospital":
      return {
        indicatorName: "beds",
        fieldValue: `${cityScene} has ${formatMockMetric(500 + index * 50)} beds`,
      };
    case "university":
      return {
        indicatorName: "enrollment",
        fieldValue: `${cityScene} has ${formatMockMetric(15_000 + index * 1_000)} students`,
      };
    case "transport_hub":
      return {
        indicatorName: "daily_ridership",
        fieldValue: `${cityScene} serves ${formatMockMetric(100_000 + index * 10_000)} daily riders`,
      };
    case "cruise_port":
      return {
        indicatorName: "annual_passenger_throughput",
        fieldValue: `${cityScene} handled ${formatMockMetric(500_000 + index * 50_000)} passengers`,
      };
    default:
      return {
        indicatorName: "annual_visits",
        fieldValue: `${cityScene} has ${formatMockMetric(1_000_000 + index * 100_000)} annual visits`,
      };
  }
}

function formatMockMetric(value: number) {
  return value.toLocaleString("en-US");
}

function withPrimaryEvidence(
  packet: ReturnType<typeof createPacket>,
  indicatorName: string,
  fieldValue: string,
) {
  return withEvidence(packet, [
    {
      field_group: indicatorName,
      indicator_name: indicatorName,
      field_value: fieldValue,
    },
  ]);
}

function withEvidence(
  packet: ReturnType<typeof createPacket>,
  evidenceRows: Array<{
    field_group: string;
    indicator_name: string;
    field_value: string;
  }>,
) {
  const template = packet.evidence[0];
  return {
    ...packet,
    evidence: evidenceRows.map((row) => ({
      ...template,
      ...row,
    })),
  };
}

function isMapReadyPacket(packet: ReturnType<typeof createPacket>) {
  return packet.entity.coordinate_status === "Map Ready";
}

function mapFeature(packet: ReturnType<typeof createPacket>) {
  return {
    type: "Feature",
    geometry: {
      type: "Point",
      coordinates: [packet.entity.longitude, packet.entity.latitude],
    },
    properties: {
      property_id: packet.entity.property_id,
      property_name: packet.entity.property_name,
      country: packet.entity.country,
      city: packet.entity.city,
      scene_type: packet.entity.scene_type,
      evidence_status: packet.conclusion.evidence_status,
      value_class: packet.conclusion.value_class,
      action_class: packet.conclusion.action_class,
      recommended_solution: packet.conclusion.recommended_solution,
      main_metric_text: packet.evidence[0].field_value,
      annual_visits_est: packet.scene.annual_visits_est,
      busy_hour_traffic_gb: packet.demand.busy_hour_traffic_gb,
      review_count: packet.review_queue.length,
      source_count: packet.evidence.length,
      indoor_system_presence: packet.build_status.indoor_system_presence,
      indoor_rat: packet.build_status.indoor_rat,
      proxy_level: packet.scene.proxy_level,
      last_scan_at: "2026-05-09T09:00:00Z",
      google_maps_link: packet.entity.google_maps_link,
      geocode_precision: packet.entity.geocode_precision,
      map_source: packet.entity.map_source,
      coordinate_status: packet.entity.coordinate_status,
      hero_image_url: null,
      hero_image_alt: null,
      hero_image_source_name: null,
    },
  };
}

function rawEvidenceRow(packet: ReturnType<typeof createPacket>, index: number) {
  return {
    id: `raw-${packet.entity.property_id}`,
    region: "mock-region",
    country: packet.entity.country,
    city: packet.entity.city,
    property_name: packet.entity.property_name,
    scene_type: packet.entity.scene_type,
    source_type: index % 2 === 0 ? "firecrawl_search" : "operator_venue",
    source_url: packet.evidence[0].source_url,
    source_name: packet.evidence[0].source_name,
    source_tier: packet.evidence[0].source_tier,
    source_date: packet.evidence[0].source_date,
    field_group: packet.evidence[0].field_group,
    indicator_name: packet.evidence[0].indicator_name,
    field_value: packet.evidence[0].field_value,
    status: index % 3 === 0 ? "new" : "accepted",
    curation_needed: index % 3 === 0,
    curation_run_id: "mock-curation-run",
    curated_at: "2026-05-09T09:10:00Z",
    created_at: "2026-05-09T09:00:00Z",
  };
}

function candidateDraftRow(packet: ReturnType<typeof createPacket>, index: number) {
  return {
    id: `draft-${packet.entity.property_id}`,
    curation_run_id: "mock-curation-run",
    raw_evidence_ids: [`raw-${packet.entity.property_id}`],
    country: packet.entity.country,
    city: packet.entity.city,
    property_name: packet.entity.property_name,
    scene_type: packet.entity.scene_type,
    source_type: index % 2 === 0 ? "firecrawl_search" : "operator_venue",
    status: index % 4 === 0 ? "review" : "accepted",
    issues: index % 4 === 0 ? ["Check coordinate precision"] : [],
    candidate_payload: {
      property_name: packet.entity.property_name,
      evidence: packet.evidence,
    },
    created_at: "2026-05-09T09:12:00Z",
  };
}

function reviewQueueRows(packet: ReturnType<typeof createPacket>) {
  return packet.review_queue.map((item) => ({
    property_id: packet.entity.property_id,
    property_name: packet.entity.property_name,
    country: packet.entity.country,
    city: packet.entity.city,
    scene_type: packet.entity.scene_type,
    candidate_quality_status: "accepted",
    visibility: "visible",
    quality_issues: [],
    reason: item.reason,
    next_action: item.next_action,
    status: item.status,
    review_type: "build_status",
    severity: "medium",
    source_url: packet.evidence[0].source_url,
  }));
}

function discoveryProgressRows(packets: Array<ReturnType<typeof createPacket>>) {
  const groups = new Map<string, ReturnType<typeof createPacket>>();
  packets.forEach((packet) => {
    const key = `${packet.entity.country}-${packet.entity.scene_type}`;
    if (!groups.has(key)) {
      groups.set(key, packet);
    }
  });
  return Array.from(groups.values()).slice(0, 4).map((packet, index) => ({
    id: `progress-${packet.entity.country}-${packet.entity.scene_type}`,
    region: "mock-region",
    country: packet.entity.country,
    scene_type: packet.entity.scene_type,
    source_type: index % 2 === 0 ? "firecrawl_search" : "operator_venue",
    status: index === 3 ? "blocked_review" : "active",
    cycle_number: index + 1,
    no_new_cycles: index === 3 ? 1 : 0,
    accepted_new_total: 2 + index,
    accepted_new_last_cycle: index,
    draft_review_total: index === 3 ? 2 : 0,
    last_completed_at: "2026-05-09T09:15:00Z",
    exhausted_at: null,
    last_error: index === 3 ? "coordinate_review" : null,
    updated_at: "2026-05-09T09:15:00Z",
  }));
}

function countrySummaries(packets: Array<ReturnType<typeof createPacket>>) {
  const countries = Array.from(new Set(packets.map((packet) => packet.entity.country))).sort();
  return countries.map((country) => {
    const rows = packets.filter((packet) => packet.entity.country === country);
    return {
      country,
      candidate_count: rows.length,
      map_point_count: rows.filter(isMapReadyPacket).length,
      coordinate_review_count: rows.filter((packet) => packet.entity.coordinate_status === "Review Required").length,
      review_count: rows.reduce((total, packet) => total + packet.review_queue.length, 0),
      source_count: rows.reduce((total, packet) => total + packet.evidence.length, 0),
      scenes: Object.fromEntries(SCENES.map((scene) => [scene, rows.filter((packet) => packet.entity.scene_type === scene).length])),
    };
  });
}

function citySummaries(packets: Array<ReturnType<typeof createPacket>>) {
  const cities = new Map<string, {
    country: string;
    city: string;
    candidate_count: number;
    map_point_count: number;
    review_count: number;
    source_count: number;
    scenes: Record<string, number>;
    property_ids: string[];
    source_urls: Set<string>;
    map_lat_total: number;
    map_lng_total: number;
    map_count: number;
    all_lat_total: number;
    all_lng_total: number;
    all_count: number;
  }>();
  packets.forEach((packet) => {
    const key = `${packet.entity.country.toLowerCase()}::${packet.entity.city.toLowerCase()}`;
    const row = cities.get(key) || {
      country: packet.entity.country,
      city: packet.entity.city,
      candidate_count: 0,
      map_point_count: 0,
      review_count: 0,
      source_count: 0,
      scenes: {},
      property_ids: [],
      source_urls: new Set<string>(),
      map_lat_total: 0,
      map_lng_total: 0,
      map_count: 0,
      all_lat_total: 0,
      all_lng_total: 0,
      all_count: 0,
    };
    row.candidate_count += 1;
    row.review_count += packet.review_queue.length;
    row.scenes[packet.entity.scene_type] = (row.scenes[packet.entity.scene_type] || 0) + 1;
    row.property_ids.push(packet.entity.property_id);
    packet.evidence.forEach((item) => row.source_urls.add(item.source_url));
    row.all_lat_total += packet.entity.latitude;
    row.all_lng_total += packet.entity.longitude;
    row.all_count += 1;
    if (isMapReadyPacket(packet)) {
      row.map_point_count += 1;
      row.map_lat_total += packet.entity.latitude;
      row.map_lng_total += packet.entity.longitude;
      row.map_count += 1;
    }
    cities.set(key, row);
  });
  return Array.from(cities.values())
    .map(({ source_urls, map_lat_total, map_lng_total, map_count, all_lat_total, all_lng_total, all_count, ...row }) => ({
      ...row,
      source_count: source_urls.size,
      lat: map_count > 0 ? map_lat_total / map_count : all_lat_total / all_count,
      lng: map_count > 0 ? map_lng_total / map_count : all_lng_total / all_count,
      position_source: map_count > 0 ? "map_ready_average" : "property_average",
    }))
    .sort((left, right) => right.candidate_count - left.candidate_count || left.city.localeCompare(right.city));
}

function expectDataRequestsUseGlobalCandidatePool(dataRequests: string[]) {
  const requestedPaths = dataRequests.map((url) => new URL(url).pathname);
  expect(requestedPaths).toContain("/map/country-summary");
  const unscopedCandidatePayloads = dataRequests.filter((url) => {
    const request = new URL(url);
    return ["/map/city-summary", "/map/properties", "/properties"].includes(request.pathname)
      && !request.searchParams.has("country");
  });
  expect(unscopedCandidatePayloads).toEqual([]);
  expect(dataRequests.every((url) => !new URL(url).searchParams.has("scan_run_id"))).toBe(true);
}

function isIgnorableConsoleError(line: string): boolean {
  return (
    line.includes("favicon") ||
    line.includes("Failed to load resource: the server responded with a status of 404")
  );
}
