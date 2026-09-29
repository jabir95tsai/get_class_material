chrome.action.onClicked.addListener(async () => {
  const url = chrome.runtime.getURL("panel.html");
  const existing = (await chrome.tabs.query({url}))[0];
  if (existing) {
    await chrome.tabs.update(existing.id, {active: true});
    await chrome.windows.update(existing.windowId, {focused: true});
  } else {
    await chrome.tabs.create({url});
  }
});
