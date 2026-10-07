(() => {
  "use strict";

  const initialise = () => {
    const configNode = document.getElementById("staff-role-guide-config");
    const dialog = document.getElementById("staffRoleGuideDialog");
    if (!configNode || !dialog || dialog.dataset.initialised) return;
    dialog.dataset.initialised = "true";
    // A navigation include may sit inside a card. Keep its modal at body level.
    document.body.append(dialog);
    const config = JSON.parse(configNode.textContent);
    const accountKey = `hospital.staffGuide:${config.accountId}:${config.role}`;
    const dayKey = `${accountKey}:hiddenDay`;
    const visitKey = `${accountKey}:visit`;
    const visitValue = `${config.day}:${config.visit}`;
    const checkbox = document.getElementById("staffGuideHideToday");
    const storageNote = document.getElementById("staffGuideStorageNote");
    const welcome = document.getElementById("staffGuideWelcome");
    const reader = document.getElementById("staffGuideReader");
    const welcomeActions = document.getElementById("staffGuideWelcomeActions");
    const readerActions = document.getElementById("staffGuideReaderActions");
    const pages = Array.from(dialog.querySelectorAll("[data-guide-page]"));
    const topics = Array.from(dialog.querySelectorAll("[data-guide-topic]"));
    const previous = document.getElementById("staffGuidePrevious");
    const next = document.getElementById("staffGuideNext");
    let pageIndex = 0;
    let lastFocus = null;

    const readStorage = (storageName, key) => {
      try { return window[storageName].getItem(key); }
      catch (_) { return null; }
    };
    const writeStorage = (storageName, key, value) => {
      try {
        if (value === null) window[storageName].removeItem(key);
        else window[storageName].setItem(key, value);
        return true;
      } catch (_) {
        storageNote.hidden = false;
        return false;
      }
    };
    const rememberChoice = () => {
      writeStorage("sessionStorage", visitKey, visitValue);
      writeStorage("localStorage", dayKey, checkbox.checked ? config.day : null);
    };
    const showPage = (index, focus = true) => {
      pageIndex = Math.max(0, Math.min(index, pages.length - 1));
      pages.forEach((page, i) => { page.hidden = i !== pageIndex; });
      topics.forEach((topic, i) => {
        if (i === pageIndex) topic.setAttribute("aria-current", "step");
        else topic.removeAttribute("aria-current");
      });
      previous.disabled = pageIndex === 0;
      next.textContent = pageIndex === pages.length - 1 ? "อ่านครบแล้ว ✓" : "หัวข้อถัดไป →";
      document.getElementById("staffGuideProgress").textContent = `${pageIndex + 1} / ${pages.length}`;
      if (focus) {
        pages[pageIndex].querySelector("h3").focus({ preventScroll: true });
        document.getElementById("staffGuidePages").scrollIntoView({ block: "nearest" });
      }
    };
    const readGuide = () => {
      welcome.hidden = true;
      welcomeActions.hidden = true;
      reader.hidden = false;
      readerActions.hidden = false;
      rememberChoice();
      showPage(0);
    };
    const openGuide = (manual = false) => {
      if (dialog.open) return;
      lastFocus = document.activeElement;
      checkbox.checked = readStorage("localStorage", dayKey) === config.day;
      welcome.hidden = false;
      welcomeActions.hidden = false;
      reader.hidden = true;
      readerActions.hidden = true;
      dialog.showModal();
      // Page navigation must not repeatedly interrupt the same signed-in visit.
      writeStorage("sessionStorage", visitKey, visitValue);
      if (manual) readGuide();
      else document.getElementById("staffGuideRead").focus();
    };
    const closeGuide = () => {
      rememberChoice();
      dialog.close();
      if (lastFocus?.isConnected) lastFocus.focus({ preventScroll: true });
    };
    document.getElementById("staffRoleGuideOpen")?.addEventListener("click", () => openGuide(true));
    document.getElementById("staffGuideRead").addEventListener("click", readGuide);
    dialog.querySelectorAll("[data-guide-close]").forEach(button => button.addEventListener("click", closeGuide));
    dialog.querySelectorAll("[data-guide-link]").forEach(link => link.addEventListener("click", closeGuide));
    topics.forEach(topic => topic.addEventListener("click", () => showPage(Number(topic.dataset.guideTopic))));
    previous.addEventListener("click", () => showPage(pageIndex - 1));
    next.addEventListener("click", () => pageIndex === pages.length - 1 ? closeGuide() : showPage(pageIndex + 1));
    checkbox.addEventListener("change", rememberChoice);
    dialog.addEventListener("cancel", event => { event.preventDefault(); closeGuide(); });
    dialog.addEventListener("click", event => {
      if (event.target !== dialog) return;
      const box = dialog.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) closeGuide();
    });
    const hiddenToday = readStorage("localStorage", dayKey) === config.day;
    const seenThisVisit = readStorage("sessionStorage", visitKey) === visitValue;
    if (!hiddenToday && !seenThisVisit) openGuide();
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialise, { once: true });
  else initialise();
})();
