() => {
  let active = null;
  let remembered = null;
  const bind = () => {
    const root = document.querySelector('#study-player');
    if (active && active.root !== root) {
      active.clearWait();
      remembered = {
        videoId: active.root.dataset.videoId,
        time: active.video.currentTime,
        rate: active.video.playbackRate,
        playing: active.playing,
        repeat: active.repeat.checked,
        count: active.count.value,
        pause: active.pause.value,
        endPause: active.endPause.value,
        expanded: active.expanded,
      };
      active = null;
    }
    if (!root || root.dataset.bound === '1') return;
    root.dataset.bound = '1';
    const video = root.querySelector('video');
    const segments = JSON.parse(root.dataset.segments || '[]');
    const status = root.querySelector('.play-status');
    const repeat = root.querySelector('.repeat-mode');
    const count = root.querySelector('.repeat-count');
    const pause = root.querySelector('.pause-factor');
    const endPause = root.querySelector('.end-pause-factor');
    const speed = root.querySelector('.play-speed');
    const nextSentence = root.querySelector('.next-sentence');
    const sentenceButtons = [...root.querySelectorAll('[data-seek]')];
    const sentenceList = root.querySelector('.sentence-list');
    nextSentence.disabled = !segments.length;
    const expand = root.querySelector('.sentence-expand');
    const preferenceKey = 'video-study-player-v1';
    let preferences = {};
    try { preferences = JSON.parse(localStorage.getItem(preferenceKey) || '{}') || {}; } catch { /* Browser storage is optional. */ }
    let index = -1, played = 0, timer = null, internalSeek = false, transitioning = false;
    let viewIndex = -1, expanded = false;

    const clearWait = () => { if (timer !== null) clearTimeout(timer); timer = null; };
    active = {root, video, repeat, count, pause, endPause, clearWait, playing: false, expanded: false};
    count.value = preferences.count || count.value;
    pause.value = preferences.pause ?? pause.value;
    endPause.value = preferences.endPause ?? endPause.value;
    speed.value = preferences.speed || speed.value;
    repeat.checked = Boolean(preferences.repeat && segments.length);
    const savePreferences = () => {
      try {
        localStorage.setItem(preferenceKey, JSON.stringify({repeat: repeat.checked, count: count.value,
          pause: pause.value, endPause: endPause.value, speed: speed.value}));
      } catch { /* Playback remains usable when browser storage is unavailable. */ }
    };
    video.addEventListener('play', () => { if (active?.video === video) active.playing = true; });
    video.addEventListener('pause', () => {
      if (active?.video === video && document.contains(video)) active.playing = false;
    });
    const seek = (time) => {
      internalSeek = true;
      transitioning = true;
      video.pause();
      video.currentTime = time;
    };
    const locate = (time) => segments.findIndex((s) => time < s.end - 0.04);
    const updateWindow = (force = false) => {
      if (!sentenceButtons.length) return;
      const located = locate(video.currentTime);
      const current = repeat.checked && index >= 0 ? index : located < 0 ? sentenceButtons.length - 1 : located;
      nextSentence.disabled = current >= sentenceButtons.length - 1;
      if (!force && current === viewIndex) return;
      viewIndex = current;
      sentenceButtons.forEach((button, i) => {
        button.hidden = !expanded && Math.abs(i - current) > 3;
        button.classList.toggle('active', i === current);
        if (i === current) button.setAttribute('aria-current', 'true');
        else button.removeAttribute('aria-current');
      });
      const currentButton = sentenceButtons[current];
      sentenceList.scrollTop = currentButton.offsetTop - sentenceList.offsetTop - sentenceList.clientHeight / 2 + currentButton.clientHeight / 2;
    };
    expand.addEventListener('click', () => {
      expanded = !expanded;
      active.expanded = expanded;
      expand.textContent = expanded ? '收起' : '展开全部';
      expand.setAttribute('aria-expanded', String(expanded));
      updateWindow(true);
    });
    nextSentence.addEventListener('click', () => {
      if (!segments.length) return;
      const current = repeat.checked && index >= 0 ? index : locate(video.currentTime);
      if (current < 0 || current >= segments.length - 1) return;
      clearWait();
      index = current + 1;
      played = 0;
      seek(segments[index].start);
      show();
      updateWindow(true);
    });
    const show = () => {
      status.textContent = repeat.checked && index >= 0 && index < segments.length
        ? `${index + 1}/${segments.length} · 第 ${played + 1}/${count.value} 遍 · ${segments[index].text}` :'';
    };
    const next = () => {
      clearWait();
      index += 1;
      played = 0;
      if (index >= segments.length) {
        repeat.checked = false;
        show();
        return;
      }
      seek(segments[index].start);
      show();
    };
    video.addEventListener('seeked', () => {
      if (internalSeek) {
        internalSeek = false;
        transitioning = false;
        video.play().catch(() => {});
        return;
      }
      clearWait();
      if (repeat.checked) { index = locate(video.currentTime); played = 0; show(); }
      updateWindow();
    });
    video.addEventListener('timeupdate', () => {
      updateWindow();
      if (!repeat.checked || transitioning || timer !== null || !segments.length || (video.paused && !video.ended)) return;
      if (index < 0) { index = locate(video.currentTime); played = 0; show(); }
      if (index < 0 || index >= segments.length) { repeat.checked = false; show(); return; }
      const segment = segments[index];
      if (video.currentTime < segment.end - 0.04) return;
      video.pause();
      const factor = played + 1 < Number(count.value) ? Number(pause.value) : Number(endPause.value);
      const delay = (segment.end - segment.start) * factor / video.playbackRate * 1000;
      timer = setTimeout(() => {
        timer = null;
        if (!repeat.checked) return;
        if (played + 1 < Number(count.value)) {
          played += 1;
          seek(segment.start);
          show();
        } else next();
      }, delay);
    });
    repeat.addEventListener('change', () => {
      clearWait(); index = repeat.checked ? locate(video.currentTime) : -1; played = 0;
      if (repeat.checked && !segments.length) { repeat.checked = false; status.textContent = '请先生成字幕'; return; }
      if (repeat.checked && index < 0) { index = 0; seek(segments[0].start); }
      savePreferences();
      updateWindow(true);
      show();
    });
    for (const control of [count, pause, endPause]) control.addEventListener('change', savePreferences);
    speed.addEventListener('change', () => { video.playbackRate = Number(speed.value); savePreferences(); });
    video.addEventListener('loadedmetadata', () => { video.playbackRate = Number(speed.value); });
    root.querySelectorAll('[data-seek]').forEach((button) => button.addEventListener('click', () => {
      clearWait(); index = Number(button.dataset.seek); played = 0;
      seek(segments[index].start); show(); updateWindow();
    }));
    if (remembered) {
      const previous = remembered;
      remembered = null;
      if (previous.videoId === root.dataset.videoId) {
        count.value = previous.count;
        pause.value = previous.pause;
        endPause.value = previous.endPause;
        speed.value = String(previous.rate);
        repeat.checked = previous.repeat && segments.length > 0;
        expanded = previous.expanded;
        active.expanded = expanded;
        expand.textContent = expanded ? '收起' : '展开全部';
        expand.setAttribute('aria-expanded', String(expanded));
        index = repeat.checked ? locate(previous.time) : -1;
        const restore = () => {
          video.playbackRate = previous.rate;
          video.currentTime = Math.min(previous.time, video.duration || previous.time);
          if (previous.playing) video.play().catch(() => {});
        };
        if (video.readyState >= 1) restore();
        else video.addEventListener('loadedmetadata', restore, {once: true});
      }
    }
    if (repeat.checked && index < 0 && segments.length) index = locate(video.currentTime);
    updateWindow(true);
    show();
  };
  new MutationObserver(bind).observe(document.body, {childList: true, subtree: true});
  bind();
}
