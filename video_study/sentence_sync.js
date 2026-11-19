(current) => {
  const root = document.querySelector('#study-player');
  if (!root) return null;
  const video = root.querySelector('video');
  const segments = JSON.parse(root.dataset.segments || '[]');
  if (!video || !segments.length) return null;
  const prefix = `${root.dataset.videoId}:`;
  // The HTML and Dropdown can render at different instants during a detail update.
  // Wait for the Dropdown's own choices/value update before syncing this video.
  if (typeof current !== 'string' || !current.startsWith(prefix)) return current;
  const index = segments.findIndex(s => video.currentTime < s.end - 0.04);
  return prefix + (index < 0 ? segments.length - 1 : index);
}
