const openButton = document.getElementById("open-launch-dialog");
const dialog = document.getElementById("launch-dialog");
const closeButton = document.getElementById("close-launch-dialog");
const launchFrame = dialog.querySelector("iframe");

openButton.addEventListener("click", (event) => {
  event.preventDefault();
  launchFrame.src = openButton.href;
  dialog.showModal();
});

closeButton.addEventListener("click", () => {
  dialog.close();
});

dialog.addEventListener("close", () => {
  launchFrame.src = "about:blank";
  openButton.focus();
});

dialog.addEventListener("click", (event) => {
  if (event.target === dialog) dialog.close();
});
