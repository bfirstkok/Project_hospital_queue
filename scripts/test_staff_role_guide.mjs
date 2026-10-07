// Run with: node --test scripts/test_staff_role_guide.mjs
// jsdom may be provided via STAFF_GUIDE_TEST_NODE_MODULES or patient-portal/node_modules.
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
const jsdomPath = require.resolve("jsdom", {
  paths: [
    process.env.STAFF_GUIDE_TEST_NODE_MODULES,
    path.resolve(projectRoot, "../patient-portal/node_modules"),
  ].filter(Boolean),
});
const { JSDOM } = require(jsdomPath);
const source = fs.readFileSync(path.join(projectRoot, "static/js/staff-role-guide.js"), "utf8");

const storage = (values = new Map()) => ({
  getItem: key => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: key => values.delete(key),
});

function openPage({
  accountId = "17", role = "NURSE", day = "2026-10-06", visit = "first-login",
  local = new Map(), session = new Map(), blockedStorage = false,
} = {}) {
  const config = { accountId, role, day, visit };
  const dom = new JSDOM(`<!doctype html><html><body>
    <button id="staffRoleGuideOpen">คู่มือการใช้งาน</button>
    <script id="staff-role-guide-config" type="application/json">${JSON.stringify(config)}</script>
    <dialog id="staffRoleGuideDialog">
      <button data-guide-close id="headerClose">ปิด</button>
      <section id="staffGuideWelcome">ยินดีต้อนรับ</section>
      <section id="staffGuideReader" hidden>
        <div id="staffGuidePages">${Array.from({ length: 3 }, (_, index) => `
          <article data-guide-page="${index}" ${index ? "hidden" : ""}>
            <h3 tabindex="-1">ขั้นตอน ${index + 1}</h3>
            <a data-guide-link href="/accounts/permissions/">เปิดหน้างาน</a>
          </article>`).join("")}</div>
        ${Array.from({ length: 3 }, (_, index) => `<button data-guide-topic="${index}">หัวข้อ ${index + 1}</button>`).join("")}
      </section>
      <input type="checkbox" id="staffGuideHideToday">
      <p id="staffGuideStorageNote" hidden>จำการตั้งค่าไม่ได้</p>
      <div id="staffGuideWelcomeActions">
        <button data-guide-close id="skip">ข้าม</button><button id="staffGuideRead">อ่านคู่มือ</button>
      </div>
      <div id="staffGuideReaderActions" hidden>
        <button id="staffGuidePrevious">ก่อนหน้า</button>
        <span id="staffGuideProgress"></span><button id="staffGuideNext">ถัดไป</button>
      </div>
    </dialog>
  </body></html>`, { url: "https://hospital.example/accounts/permissions/", runScripts: "outside-only" });
  const { window } = dom;
  const document = window.document;
  for (const [name, values] of [["localStorage", local], ["sessionStorage", session]]) {
    Object.defineProperty(window, name, blockedStorage
      ? { get() { throw new window.DOMException("Storage blocked", "SecurityError"); } }
      : { value: storage(values) });
  }
  // jsdom has no dialog top layer or scrolling; retain their observable state.
  const dialog = document.getElementById("staffRoleGuideDialog");
  dialog.showModal = () => { dialog.open = true; };
  dialog.close = () => { dialog.open = false; };
  window.HTMLElement.prototype.scrollIntoView = () => {};
  window.eval(source);
  document.dispatchEvent(new window.Event("DOMContentLoaded"));
  return {
    dom, window, document, dialog, local, session,
    get: id => document.getElementById(id),
    hideToday(checked = true) {
      const checkbox = document.getElementById("staffGuideHideToday");
      checkbox.checked = checked;
      checkbox.dispatchEvent(new window.Event("change", { bubbles: true }));
    },
  };
}

test("auto-opens once per login; unchecked skip survives page navigation and a new login opens", () => {
  const local = new Map(), session = new Map();
  const first = openPage({ local, session });
  assert.equal(first.dialog.open, true);
  assert.equal(first.get("staffGuideWelcome").hidden, false);
  assert.equal(first.document.activeElement, first.get("staffGuideRead"));
  first.get("skip").click();
  assert.equal(first.dialog.open, false);
  first.dom.window.close();

  const navigation = openPage({ local, session });
  assert.equal(navigation.dialog.open, false);
  navigation.dom.window.close();

  const nextLogin = openPage({ local, session, visit: "second-login" });
  assert.equal(nextLogin.dialog.open, true);
  nextLogin.dom.window.close();
});

test("checking hide today suppresses later logins but the next Bangkok calendar day opens", () => {
  const local = new Map(), session = new Map();
  const first = openPage({ local, session });
  first.hideToday();
  first.get("skip").click();
  first.dom.window.close();

  const sameDay = openPage({ local, session, visit: "second-login" });
  assert.equal(sameDay.dialog.open, false);
  sameDay.dom.window.close();

  const nextDay = openPage({ local, session, day: "2026-10-07", visit: "second-login" });
  assert.equal(nextDay.dialog.open, true);
  assert.equal(nextDay.get("staffGuideHideToday").checked, false);
  nextDay.dom.window.close();
});

test("hide today is isolated by account and effective role", () => {
  const local = new Map(), session = new Map();
  const nurse = openPage({ local, session });
  nurse.hideToday();
  nurse.get("skip").click();
  nurse.dom.window.close();

  const secondAccount = openPage({ local, session, accountId: "18" });
  assert.equal(secondAccount.dialog.open, true);
  secondAccount.dom.window.close();

  const assumedRole = openPage({ local, session, role: "PHARMACIST" });
  assert.equal(assumedRole.dialog.open, true);
  assumedRole.dom.window.close();

  const originalRole = openPage({ local, session, visit: "third-login" });
  assert.equal(originalRole.dialog.open, false);
  originalRole.dom.window.close();
});

test("sidebar reopen works despite daily suppression and can restore the daily prompt", () => {
  const local = new Map(), session = new Map();
  const first = openPage({ local, session });
  first.hideToday();
  first.get("skip").click();
  first.dom.window.close();

  const manual = openPage({ local, session, visit: "second-login" });
  assert.equal(manual.dialog.open, false);
  manual.get("staffRoleGuideOpen").focus();
  manual.get("staffRoleGuideOpen").click();
  assert.equal(manual.dialog.open, true);
  assert.equal(manual.get("staffGuideReader").hidden, false);
  assert.equal(manual.get("staffGuideWelcome").hidden, true);
  assert.equal(manual.get("staffGuideHideToday").checked, true);
  manual.hideToday(false);
  manual.get("headerClose").click();
  assert.equal(manual.document.activeElement, manual.get("staffRoleGuideOpen"));
  manual.dom.window.close();

  const nextLogin = openPage({ local, session, visit: "third-login" });
  assert.equal(nextLogin.dialog.open, true);
  nextLogin.dom.window.close();
});

test("reader next/previous/topic navigation updates pages and finishing closes the guide", () => {
  const page = openPage();
  page.get("staffGuideRead").click();
  const articles = Array.from(page.document.querySelectorAll("[data-guide-page]"));
  const topics = Array.from(page.document.querySelectorAll("[data-guide-topic]"));
  assert.equal(page.get("staffGuideReader").hidden, false);
  assert.equal(page.get("staffGuidePrevious").disabled, true);
  assert.equal(page.get("staffGuideProgress").textContent, "1 / 3");
  page.get("staffGuideNext").click();
  assert.deepEqual(articles.map(article => article.hidden), [true, false, true]);
  assert.equal(topics[1].getAttribute("aria-current"), "step");
  assert.equal(page.get("staffGuidePrevious").disabled, false);
  page.get("staffGuidePrevious").click();
  assert.equal(articles[0].hidden, false);
  topics[2].click();
  assert.deepEqual(articles.map(article => article.hidden), [true, true, false]);
  assert.equal(page.get("staffGuideProgress").textContent, "3 / 3");
  assert.equal(page.get("staffGuideNext").textContent, "อ่านครบแล้ว ✓");
  page.get("staffGuideNext").click();
  assert.equal(page.dialog.open, false);
  page.dom.window.close();
});

test("following a workflow link closes the guide and preserves today's choice", () => {
  const page = openPage();
  page.hideToday();
  page.get("staffGuideRead").click();
  const link = page.document.querySelector("[data-guide-link]");
  // Avoid jsdom's unimplemented cross-document navigation after the click.
  link.addEventListener("click", event => event.preventDefault());
  link.click();
  assert.equal(page.dialog.open, false);
  assert.equal(page.local.get("hospital.staffGuide:17:NURSE:hiddenDay"), "2026-10-06");
  page.dom.window.close();
});

test("Escape closes the guide while keeping the checked preference", () => {
  const page = openPage();
  page.hideToday();
  const event = new page.window.Event("cancel", { cancelable: true });
  page.dialog.dispatchEvent(event);
  assert.equal(event.defaultPrevented, true);
  assert.equal(page.dialog.open, false);
  assert.equal(page.local.get("hospital.staffGuide:17:NURSE:hiddenDay"), "2026-10-06");
  page.dom.window.close();
});

test("blocked browser storage still permits reading, skipping and reopening", () => {
  const page = openPage({ blockedStorage: true });
  assert.equal(page.dialog.open, true);
  assert.equal(page.get("staffGuideStorageNote").hidden, false);
  assert.doesNotThrow(() => {
    page.hideToday();
    page.get("staffGuideRead").click();
    page.get("staffGuideNext").click();
    page.get("headerClose").click();
    page.get("staffRoleGuideOpen").click();
  });
  assert.equal(page.dialog.open, true);
  assert.equal(page.get("staffGuideReader").hidden, false);
  page.dom.window.close();
});

test("pages without a staff guide initialize harmlessly", () => {
  const dom = new JSDOM("<!doctype html><html><body>Login</body></html>", { runScripts: "outside-only" });
  assert.doesNotThrow(() => {
    dom.window.eval(source);
    dom.window.document.dispatchEvent(new dom.window.Event("DOMContentLoaded"));
  });
  dom.window.close();
});
