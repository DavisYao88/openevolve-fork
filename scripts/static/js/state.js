export let width = window.innerWidth;
export let height = window.innerHeight;

export function setWidth(w) { width = w; }
export function setHeight(h) { height = h; }
export function updateDimensions() {
    width = window.innerWidth;
    const toolbar = document.getElementById('toolbar');
    const toolbarHeight = toolbar ? toolbar.offsetHeight : 0;
    height = window.innerHeight - toolbarHeight;
    document.documentElement.style.setProperty('--toolbar-height', `${toolbarHeight}px`);
}

const toolbar = document.getElementById('toolbar');
if (toolbar && 'ResizeObserver' in window) {
    new ResizeObserver(updateDimensions).observe(toolbar);
}
window.addEventListener('resize', updateDimensions);
updateDimensions();
