() => {
  let active = null;
  let remembered = null;
  const bind = () => {
    const root = document.querySelector('#study-player');
    const videoId = root?.dataset.videoId;
    const segmentData = root?.dataset.segments;
    const media = root?.querySelector('video');
    if (active && (active.root !== root || active.video !== media ||
        active.videoId !== videoId || active.segmentData !== segmentData)) {
      // Snapshot identity before a framework update can reuse these DOM nodes.
      remembered = {
        videoId: active.videoId,
        time: active.video.currentTime,
        rate: active.video.playbackRate,
        playing: active.playing,
        repeat: active.repeat.checked,
        count: active.count.value,
        pause: active.pause.value,
        endPause: active.endPause.value,
        skipWords: active.skipWords.value,
        expanded: active.expanded,
        merge: active.merge.checked,
        skipEnabled: active.skipMode.checked,
        mergeMinWords: active.mergeMinWords.value,
        mergeMaxWords: active.mergeMaxWords.value,
        settingsOpen: active.settings.open,
      };
      active.dispose();
      active = null;
    }
    if (!root || active) return;
    const video = root.querySelector('video');
    const rawSegments = JSON.parse(root.dataset.segments || '[]');
    let segments = [], sourceToGroup = [];
    const merge = root.querySelector('.merge-mode');
    const skipMode = root.querySelector('.skip-mode');
    const mergeMinWords = root.querySelector('.merge-min-words');
    const mergeMaxWords = root.querySelector('.merge-max-words');
    const normalizeWords = (value, fallback, max) => {
      const n = Number(value);
      return Number.isFinite(n) && n > 0 ? Math.min(max, Math.max(1, Math.floor(n))) : fallback;
    };
    const normalizeWordLimits = () => {
      mergeMinWords.value = String(normalizeWords(mergeMinWords.value, 8, 100));
      mergeMaxWords.value = String(Math.max(Number(mergeMinWords.value), normalizeWords(mergeMaxWords.value, 20, 200)));
    };
    const countWords = text => (text.match(/[\p{L}\p{N}]+(?:['’\-][\p{L}\p{N}]+)*/gu) || []).length;
    const settings = root.querySelector('.player-settings');
    const status = root.querySelector('.play-status');
    const repeat = root.querySelector('.repeat-mode');
    const count = root.querySelector('.repeat-count');
    const pause = root.querySelector('.pause-factor');
    const endPause = root.querySelector('.end-pause-factor');
    const speed = root.querySelector('.play-speed');
    const skipWords = root.querySelector('.skip-words');
    const normalizeThreshold = (value) => {
      const number = Number(value);
      return Number.isFinite(number) ? Math.max(0, Math.floor(number)) : 0;
    };
    const speakerOf = cue => {
      const value = String(cue.speaker || '').trim();
      return ['unknown', 'unk', 'none'].includes(value.toLowerCase()) ? '' : value;
    };
    const adjacent = (left, right) => left && right && right.start >= left.start &&
      right.start - left.end <= 1.5;
    const sameTurn = (left, right) => adjacent(left, right) &&
      speakerOf(left) !== '' && speakerOf(left) === speakerOf(right);
    const rebuildGroups = () => {
      segments = [];
      sourceToGroup = [];
      for (const [i, cue] of rawSegments.entries()) {
        const words = countWords(cue.text);
        const speaker = speakerOf(cue);
        // Route using original neighbors, even when word limits split a turn.
        const continuous = sameTurn(rawSegments[i - 1], cue) || sameTurn(cue, rawSegments[i + 1]);
        const mergeRoute = merge.checked && (continuous || !speaker);
        const skipped = skipMode.checked && !(merge.checked && continuous) &&
          words < normalizeThreshold(skipWords.value);
        const previous = segments.at(-1);
        // Never bridge a skipped cue: its audio would otherwise re-enter the group.
        if (mergeRoute && !skipped && previous?.mergeRoute && !previous.skipped &&
            previous.speaker === speaker && previous.last === i - 1 &&
            adjacent(rawSegments[i - 1], cue) &&
            previous.words < Number(mergeMinWords.value) &&
            previous.words + words <= Number(mergeMaxWords.value)) {
          previous.end = Math.max(previous.end, cue.end);
          previous.text += ' ' + cue.text;
          previous.last = i;
          previous.words += words;
        } else {
          segments.push({...cue, speaker, words, skipped, mergeRoute, first: i, last: i});
        }
        sourceToGroup[i] = segments.length - 1;
      }
    };
    const shouldSkip = i => Boolean(segments[i]?.skipped);
    const playableIndex = (start) => {
      while (start < segments.length && shouldSkip(start)) start += 1;
      return start;
    };
    const nextSentence = root.querySelector('.next-sentence');
    const sentenceButtons = [...root.querySelectorAll('[data-seek]')];
    const sentenceList = root.querySelector('.sentence-list');
    nextSentence.disabled = !rawSegments.length;
    const expand = root.querySelector('.sentence-expand');
    const preferenceKey = 'video-study-player-v1';
    let preferences = {};
    try { preferences = JSON.parse(localStorage.getItem(preferenceKey) || '{}') || {}; } catch { /* Browser storage is optional. */ }
    let index = -1, played = 0, timer = null, internalSeek = false, transitioning = false;
    let viewIndex = -1, expanded = false;

    const controller = new AbortController();
    const on = (target, event, callback, options = {}) =>
      target.addEventListener(event, callback, {...options, signal: controller.signal});
    const clearWait = () => { if (timer !== null) clearTimeout(timer); timer = null; };
    active = {root, video, videoId, segmentData, repeat, count, pause, endPause, skipWords, skipMode, merge, mergeMinWords, mergeMaxWords, settings, clearWait,
      playing: false, expanded: false, dispose: () => {
        clearWait();
        controller.abort();
        internalSeek = false;
        transitioning = false;
        video.pause();
      }};
    video.pause();
    count.value = preferences.count || count.value;
    pause.value = preferences.pause ?? pause.value;
    endPause.value = preferences.endPause ?? endPause.value;
    speed.value = preferences.speed || speed.value;
    skipWords.value = String(normalizeThreshold(preferences.skipWords));
    merge.checked = preferences.merge !== false;
    skipMode.checked = preferences.skipEnabled !== false;
    mergeMinWords.value = preferences.mergeMinWords ?? 8;
    mergeMaxWords.value = preferences.mergeMaxWords ?? 20;
    normalizeWordLimits();
    settings.open = preferences.settingsOpen !== false;
    rebuildGroups();
    repeat.checked = Boolean(preferences.repeat && segments.length);
    const savePreferences = () => {
      try {
        localStorage.setItem(preferenceKey, JSON.stringify({repeat: repeat.checked, count: count.value,
          pause: pause.value, endPause: endPause.value, speed: speed.value, skipWords: normalizeThreshold(skipWords.value),
          merge: merge.checked, skipEnabled: skipMode.checked,
          mergeMinWords: Number(mergeMinWords.value), mergeMaxWords: Number(mergeMaxWords.value), settingsOpen: settings.open}));
      } catch { /* Playback remains usable when browser storage is unavailable. */ }
    };
    on(settings, 'toggle', savePreferences);
    on(video, 'play', () => {
      if (active?.video === video) active.playing = true;
      skipCurrent();
    });
    on(video, 'pause', () => {
      if (active?.video === video && document.contains(video)) active.playing = false;
    });
    const seek = (time) => {
      if (Math.abs(video.currentTime - time) < 0.001) {
        video.play().catch(() => {});
        return;
      }
      internalSeek = true;
      transitioning = true;
      video.pause();
      video.currentTime = time;
    };
    const locate = (time) => segments.findIndex((s) => time < s.end - 0.04);
    const updateWindow = (force = false) => {
      if (!sentenceButtons.length) return;
      const located = locate(video.currentTime);
      const current = repeat.checked && index >= 0 ? index : located < 0 ? segments.length - 1 : located;
      const group = segments[current];
      if (!group) return;
      nextSentence.disabled = playableIndex(current + 1) >= segments.length;
      if (!force && current === viewIndex) return;
      viewIndex = current;
      sentenceButtons.forEach((button, i) => {
        button.hidden = !expanded && (i < group.first - 3 || i > group.last + 3);
        button.classList.toggle('active', i >= group.first && i <= group.last);
        if (i === group.first) button.setAttribute('aria-current', 'true');
        else button.removeAttribute('aria-current');
      });
      const currentButton = sentenceButtons[group.first];
      sentenceList.scrollTop = currentButton.offsetTop - sentenceList.offsetTop - sentenceList.clientHeight / 2 + currentButton.clientHeight / 2;
    };
    on(expand, 'click', () => {
      expanded = !expanded;
      active.expanded = expanded;
      expand.textContent = expanded ? '收起' : '展开全部';
      expand.setAttribute('aria-expanded', String(expanded));
      updateWindow(true);
    });
    on(nextSentence, 'click', () => {
      if (!segments.length) return;
      const current = repeat.checked && index >= 0 ? index : locate(video.currentTime);
      if (current < 0 || current >= segments.length - 1) return;
      clearWait();
      jumpToSentence(current + 1);
    });
    const show = () => {
      status.textContent = repeat.checked && index >= 0 && index < segments.length
        ? `${index + 1}/${segments.length}${merge.checked ? ` 组（原字幕 ${rawSegments.length} 句）` : " 句"} · 第 ${played + 1}/${count.value} 遍 · ${segments[index].text}`: '';
    };
    const jumpToSentence = (start) => {
      clearWait();
      index = playableIndex(start);
      played = 0;
      if (index >= segments.length) {
        // No eligible subtitle remains: stop rather than seek/play in a loop.
        internalSeek = false;
        transitioning = false;
        video.pause();
        repeat.checked = false;
        const end = segments.at(-1)?.end ?? video.currentTime;
        video.currentTime = Number.isFinite(video.duration) ? Math.min(end, video.duration) : end;
        index = -1;
        updateWindow(true);
        status.textContent = start < segments.length ? '后续字幕均已跳过' : '';
        return;
      }
      seek(segments[index].start);
      show();
      updateWindow(true);
    };
    const skipCurrent = () => {
      if (transitioning || !segments.length || (video.paused && timer === null)) return false;
      // locate() also points at the next cue during a gap; only skip once it starts.
      const current = repeat.checked && index >= 0 ? index : locate(video.currentTime);
      if (current < 0 || current >= segments.length || video.currentTime < segments[current].start || !shouldSkip(current)) return false;
      jumpToSentence(current);
      return true;
    };
    const next = () => jumpToSentence(index + 1);
    on(video, 'seeked', () => {
      if (internalSeek) {
        internalSeek = false;
        transitioning = false;
        video.play().catch(() => {});
        return;
      }
      clearWait();
      if (repeat.checked) { index = locate(video.currentTime); played = 0; show(); }
      if (skipCurrent()) return;
      updateWindow();
    });
    on(video, 'timeupdate', () => {
      if (skipCurrent()) return;
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
    on(repeat, 'change', () => {
      clearWait(); index = repeat.checked ? locate(video.currentTime) : -1; played = 0;
      if (repeat.checked && !segments.length) { repeat.checked = false; status.textContent = '请先生成字幕'; return; }
      if (repeat.checked && index < 0) { jumpToSentence(0); }
      savePreferences();
      updateWindow(true);
      show();
    });
    const updateMergeLabel = () => {
      skipWords.disabled = !skipMode.checked;
      mergeMinWords.disabled = !merge.checked;
      mergeMaxWords.disabled = !merge.checked;
      nextSentence.textContent = merge.checked ? '下一组' : '下一句';
      nextSentence.title = merge.checked ? '跳转到下一组字幕' : '跳转到下一句';
    };
    const updateGrouping = () => {
      normalizeWordLimits();
      const shouldResume = !video.paused || timer !== null || internalSeek;
      const oldCurrent = repeat.checked && index >= 0 ? index : locate(video.currentTime);
      const sourceIndex = segments[oldCurrent]?.first;
      clearWait();
      internalSeek = false;
      transitioning = false;
      rebuildGroups();
      index = repeat.checked ? locate(video.currentTime) : -1;
      played = 0;
      viewIndex = -1;
      updateMergeLabel();
      savePreferences();
      updateWindow(true);
      show();
      if (shouldResume) {
        const located = locate(video.currentTime);
        const resumeIndex = located >= 0 ? located : sourceToGroup[sourceIndex];
        if (shouldSkip(resumeIndex)) jumpToSentence(resumeIndex);
        else if (!skipCurrent()) video.play().catch(() => {});
      }
    };
    on(merge, 'change', updateGrouping);
    on(skipMode, 'change', updateGrouping);
    for (const [control, max] of [[mergeMinWords, 100], [mergeMaxWords, 200]]) {
      on(control, 'input', () => {
        const n = Number(control.value);
        if (Number.isInteger(n) && n >= 1 && n <= max &&
            Number(mergeMaxWords.value) >= Number(mergeMinWords.value)) updateGrouping();
      });
      on(control, 'change', updateGrouping);
    }
    for (const control of [count, pause, endPause]) on(control, 'change', savePreferences);
    const updateSkipWords = (event) => {
      if (event.type === 'change') skipWords.value = String(normalizeThreshold(skipWords.value));
      updateGrouping();
    };
    on(skipWords, 'input', updateSkipWords);
    on(skipWords, 'change', updateSkipWords);
    on(speed, 'change', () => { video.playbackRate = Number(speed.value); savePreferences(); });
    on(video, 'loadedmetadata', () => { video.playbackRate = Number(speed.value); });
    root.querySelectorAll('[data-seek]').forEach((button) => on(button, 'click', () => {
      jumpToSentence(sourceToGroup[Number(button.dataset.seek)]);
    }));
    if (remembered) {
      const previous = remembered;
      remembered = null;
      if (previous.videoId === root.dataset.videoId) {
        count.value = previous.count;
        pause.value = previous.pause;
        endPause.value = previous.endPause;
        skipWords.value = previous.skipWords;
        merge.checked = previous.merge;
        skipMode.checked = previous.skipEnabled;
        mergeMinWords.value = previous.mergeMinWords;
        mergeMaxWords.value = previous.mergeMaxWords;
        normalizeWordLimits();
        settings.open = previous.settingsOpen;
        rebuildGroups();
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
        else on(video, 'loadedmetadata', restore, {once: true});
      }
    }
    updateMergeLabel();
    if (repeat.checked && index < 0 && segments.length) index = locate(video.currentTime);
    updateWindow(true);
    show();
  };
  new MutationObserver(bind).observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['data-video-id', 'data-segments', 'src']});
  bind();
}
