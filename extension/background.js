// background.js (MV3 service worker, no DOM access)
chrome.runtime.onInstalled.addListener(async () => {
  const cur = await chrome.storage.local.get(["serverUrl", "bridgeToken", "model"]);
  await chrome.storage.local.set({
    serverUrl: cur.serverUrl || "http://127.0.0.1:8787",
    bridgeToken: cur.bridgeToken || "",
    model: cur.model || ""
  });
  try {
    await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  } catch (e) {
    console.warn("sidePanel behavior not set:", e);
  }
});

chrome.action.onClicked.addListener(async (tab) => {
  try {
    await chrome.sidePanel.open({ tabId: tab.id });
  } catch (e) {
    console.warn("sidePanel.open failed:", e);
  }
});
