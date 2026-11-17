// ==UserScript==
// @name         Bilibili Collection ID Export
// @name:zh-CN   B站合集视频标识导出
// @namespace    local.bilibili-episode-export
// @version      2.1.0
// @description  Export bangumi episodes, video parts, UGC seasons, or loaded list links.
// @match        https://www.bilibili.com/bangumi/play/*
// @match        https://www.bilibili.com/video/*
// @match        https://www.bilibili.com/list/*
// @match        https://www.bilibili.com/medialist/play/*
// @match        https://space.bilibili.com/*
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @connect      api.bilibili.com
// ==/UserScript==

(() => {
  'use strict';

  const BVID = /^BV[0-9A-Za-z]{10}$/;

  function sourceFor(href) {
    const url = new URL(href);
    const path = url.pathname;
    if (url.hostname === 'www.bilibili.com') {
      const bangumi = /^\/bangumi\/play\/(ep|ss)(\d+)(?:\/|$)/.exec(path);
      if (bangumi) return { type: 'bangumi', id: bangumi[2], query: `${bangumi[1] === 'ep' ? 'ep_id' : 'season_id'}=${bangumi[2]}` };
      const video = /^\/video\/(BV[0-9A-Za-z]{10}|av\d+)(?:\/|$)/i.exec(path);
      if (video) return { type: 'video', id: video[1], query: `${/^BV/i.test(video[1]) ? 'bvid' : 'aid'}=${/^BV/i.test(video[1]) ? video[1] : video[1].slice(2)}` };
      if (/^\/list\/\d+/.test(path)) {
        const bvid = url.searchParams.get('bvid');
        return { type: url.searchParams.has('sid') ? 'list' : 'space_list',
          id: path.split('/')[2], query: BVID.test(bvid || '') ? `bvid=${bvid}` : null };
      }
      const medialist = /^\/medialist\/play\/ml(\d+)/.exec(path);
      if (medialist) return { type: 'visible', id: `ml${medialist[1]}` };
    }
    if (url.hostname === 'space.bilibili.com' && /^\/\d+\/(?:lists|favlist|channel)/.test(path)) {
      return { type: 'visible', id: `space-${path.split('/')[1]}` };
    }
    return null;
  }

  if (!sourceFor(location.href)) return;

  const host = document.createElement('div');
  host.style.cssText = 'position:fixed;right:16px;top:80px;z-index:2147483647';
  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = `
    <style>
      * { box-sizing: border-box; }
      .panel { width: 230px; max-width: calc(100vw - 32px); padding: 8px; border: 1px solid #76a79c; border-radius: 5px; background: #19252a; color: white; font: 13px/1.4 system-ui,sans-serif; box-shadow: 0 3px 14px #0005; }
      select, button { min-height: 32px; border-radius: 4px; font: inherit; }
      select { width: 100%; margin-bottom: 6px; border: 1px solid #76a79c; background: #fff; color: #142325; }
      button { width: 100%; border: 1px solid #76a79c; background: #194b48; color: white; cursor: pointer; }
      button:disabled { opacity: .6; cursor: default; }
      button:focus-visible, select:focus-visible { outline: 2px solid #f5d46b; outline-offset: 2px; }
      small { display: block; min-height: 16px; margin-top: 5px; overflow-wrap: anywhere; }
    </style>
    <div class="panel" role="group" aria-label="B站合集导出">
      <select aria-label="导出范围">
        <option value="auto">自动识别合集</option>
        <option value="pages">当前视频分 P</option>
        <option value="visible">当前页可见链接</option>
      </select>
      <button type="button" title="导出视频标识 JSON">导出视频标识</button>
      <small role="status" aria-live="polite"></small>
    </div>`;
  document.documentElement.append(host);
  const button = root.querySelector('button');
  const modeSelect = root.querySelector('select');
  const status = root.querySelector('small');
  modeSelect.disabled = ['bangumi', 'visible'].includes(sourceFor(location.href).type);

  function requestJSON(url) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'GET', url, responseType: 'json', timeout: 10000,
        onload: response => {
          if (response.status !== 200) return reject(new Error(`HTTP ${response.status}`));
          const data = response.response;
          if (!data || data.code !== 0 || !data.data && !data.result) {
            return reject(new Error(data?.message || '接口响应格式不正确'));
          }
          resolve(data.result || data.data);
        },
        onerror: () => reject(new Error('网络请求失败')),
        ontimeout: () => reject(new Error('请求超时'))
      });
    });
  }

  function positiveInteger(value) {
    const number = Number(value);
    return Number.isSafeInteger(number) && number > 0 ? number : null;
  }

  function makeManifest(season) {
    const seasonId = positiveInteger(season.season_id);
    if (!seasonId) throw new Error('缺少有效的季标识');
    if (!Array.isArray(season.episodes) || !season.episodes.length) throw new Error('没有正片');
    const episodes = season.episodes.map((episode, index) => ({
      number: index + 1, key: String(episode.id), ep_id: positiveInteger(episode.id),
      aid: positiveInteger(episode.aid), cid: positiveInteger(episode.cid),
      bvid: String(episode.bvid || ''), label: String(episode.badge || ''),
      url: `https://www.bilibili.com/bangumi/play/ep${episode.id}`
    }));
    if (episodes.some(episode => !episode.ep_id || !episode.aid || !episode.cid || !BVID.test(episode.bvid))) {
      throw new Error('部分正片缺少有效标识');
    }
    if (new Set(episodes.map(episode => episode.ep_id)).size !== episodes.length) throw new Error('正片标识重复');
    return { collection_type: 'bangumi', collection_id: String(seasonId), season_id: seasonId,
      title: String(season.title || ''), complete: true, episodes };
  }

  function makePagesManifest(view) {
    if (!BVID.test(view.bvid || '') || !positiveInteger(view.aid) || !Array.isArray(view.pages) || !view.pages.length) {
      throw new Error('视频分 P 数据不完整');
    }
    const episodes = view.pages.map((page, index) => ({
      number: index + 1, key: `${view.bvid}-p${page.page}`,
      aid: positiveInteger(view.aid), cid: positiveInteger(page.cid), bvid: view.bvid,
      page: positiveInteger(page.page), title: String(page.part || view.title || ''), label: '',
      url: `https://www.bilibili.com/video/${view.bvid}?p=${page.page}`
    }));
    if (episodes.some(item => !item.cid || !item.page) || new Set(episodes.map(item => item.key)).size !== episodes.length) {
      throw new Error('分 P 标识缺失或重复');
    }
    return { collection_type: 'video_pages', collection_id: view.bvid,
      title: String(view.title || ''), complete: true, episodes };
  }

  function makeUgcManifest(view) {
    const season = view.ugc_season;
    if (!positiveInteger(season?.id) || !Array.isArray(season.sections)) throw new Error('没有可读的 UP 主合集');
    const seen = new Set();
    const episodes = [];
    for (const section of season.sections) {
      for (const item of section.episodes || []) {
        if (!BVID.test(item.bvid || '') || !positiveInteger(item.aid) || !positiveInteger(item.cid)) {
          throw new Error('合集视频标识缺失');
        }
        if (seen.has(item.bvid)) continue;
        seen.add(item.bvid);
        const pages = Array.isArray(item.pages) && item.pages.length ? item.pages : [{ page: 1, cid: item.cid }];
        for (const page of pages) {
          const pageNumber = positiveInteger(page.page);
          const cid = positiveInteger(page.cid);
          if (!pageNumber || !cid) throw new Error('合集分 P 标识缺失');
          episodes.push({ number: episodes.length + 1,
            key: pages.length > 1 ? `${item.bvid}-p${pageNumber}` : item.bvid,
            aid: positiveInteger(item.aid), cid, bvid: item.bvid, page: pageNumber,
            section: String(section.title || ''),
            title: String(page.part || item.title || item.arc?.title || ''),
            label: season.is_pay_season ? '付费' : '',
            url: `https://www.bilibili.com/video/${item.bvid}?p=${pageNumber}` });
        }
      }
    }
    if (!episodes.length) throw new Error('UP 主合集为空');
    if (new Set(episodes.map(item => item.key)).size !== episodes.length) throw new Error('合集分 P 标识重复');
    return { collection_type: 'ugc_season', collection_id: String(season.id),
      title: String(season.title || ''), video_count: seen.size,
      complete: seen.size === Number(season.ep_count), episodes };
  }

  function makeMediaListManifest(items, ownerId, complete, expectedCount) {
    const seen = new Set();
    const episodes = [];
    for (const item of items) {
      const bvid = item.bv_id;
      if (!BVID.test(bvid || '') || !positiveInteger(item.id)) throw new Error('UP 视频标识缺失');
      if (seen.has(bvid)) continue;
      seen.add(bvid);
      const pages = Array.isArray(item.pages) && item.pages.length ? item.pages : [];
      if (!pages.length) throw new Error('UP 视频分 P 数据缺失');
      for (const page of pages) {
        const pageNumber = positiveInteger(page.page);
        const cid = positiveInteger(page.id);
        if (!pageNumber || !cid) throw new Error('UP 视频分 P 标识缺失');
        episodes.push({ number: episodes.length + 1,
          key: pages.length > 1 ? `${bvid}-p${pageNumber}` : bvid,
          aid: positiveInteger(item.id), cid, bvid, page: pageNumber,
          title: String(page.title || item.title || ''),
          label: item.rights?.pay || item.rights?.ugc_pay ? '付费' : '',
          url: `https://www.bilibili.com/video/${bvid}?p=${pageNumber}` });
      }
    }
    if (!episodes.length) throw new Error('UP 视频列表为空');
    if (new Set(episodes.map(item => item.key)).size !== episodes.length) throw new Error('UP 视频分 P 标识重复');
    return { collection_type: 'space_videos', collection_id: String(ownerId),
      title: 'UP主的全部视频', video_count: seen.size, expected_count: expectedCount,
      complete: complete && seen.size >= expectedCount,
      note: complete && seen.size >= expectedCount ? '' : '列表接口未返回全部视频；仅包含已获取条目。', episodes };
  }

  function currentPageBvid() {
    const href = document.querySelector('h1 a[href*="/video/BV"]')?.href;
    const match = href && /\/video\/(BV[0-9A-Za-z]{10})(?:[/?]|$)/.exec(href);
    return match?.[1] || null;
  }

  async function fetchSpaceList(ownerId) {
    let cursor = currentPageBvid();
    if (!cursor) throw new Error('无法确定列表中的当前视频');
    const items = [];
    let expectedCount = 0;
    let complete = false;
    let withCurrent = true;
    for (let page = 0; page < 100; page++) {
      const query = new URLSearchParams({ type: '1', biz_id: ownerId, ps: '20',
        desc: 'true', sort_field: '1', bvid: cursor, with_current: String(withCurrent) });
      let data;
      try {
        data = await requestJSON(`https://api.bilibili.com/x/v2/medialist/resource/list?${query}`);
      } catch (error) {
        if (!items.length) throw error;
        break;
      }
      if (!Array.isArray(data.media_list) || !data.media_list.length) break;
      items.push(...data.media_list);
      expectedCount = Math.max(expectedCount, Number(data.total_count) || 0);
      status.textContent = `已读取约 ${items.length}/${expectedCount || '?'} 条视频…`;
      if (!data.has_more) {
        complete = true;
        break;
      }
      const next = data.media_list[data.media_list.length - 1].bv_id;
      if (!BVID.test(next || '') || next === cursor) break;
      cursor = next;
      withCurrent = false;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    return makeMediaListManifest(items, ownerId, complete, expectedCount);
  }

  function makeVisibleManifest(links, source) {
    const seen = new Set();
    const episodes = [];
    for (const link of links) {
      let url;
      try { url = new URL(link.href, location.href); } catch { continue; }
      const match = /^\/video\/(BV[0-9A-Za-z]{10})(?:\/|$)/.exec(url.pathname);
      if (url.hostname !== 'www.bilibili.com' || !match || seen.has(match[1])) continue;
      seen.add(match[1]);
      episodes.push({ number: episodes.length + 1, key: match[1], bvid: match[1],
        title: String(link.text || '').trim(), label: '',
        url: `https://www.bilibili.com/video/${match[1]}` });
    }
    if (!episodes.length) throw new Error('当前页未找到已加载的视频链接');
    return { collection_type: 'visible_links', collection_id: String(source.id), title: document.title || '',
      complete: false, note: '仅当前页面已加载的链接，可能包含推荐视频；下载前请核对。', episodes };
  }

  function visibleLinks() {
    const scoped = document.querySelectorAll(
      '[class*="numberListItem"] a[href], [class*="video-list"] a[href], [class*="video-item"] a[href], [class*="medialist"] a[href]'
    );
    const anchors = scoped.length ? scoped : document.querySelectorAll('main a[href*="/video/BV"]');
    return Array.from(anchors, anchor => ({ href: anchor.href, text: anchor.textContent }));
  }

  function downloadManifest(manifest) {
    const blob = new Blob([JSON.stringify(manifest, null, 2) + '\n'], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `bilibili-${manifest.collection_type}-${manifest.collection_id}-episodes.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  button.addEventListener('click', async () => {
    button.disabled = true;
    status.textContent = '正在提取视频列表…';
    try {
      const source = sourceFor(location.href);
      if (!source) throw new Error('当前页面不支持导出');
      let manifest;
      if (modeSelect.value === 'visible' || source.type === 'visible') {
        manifest = makeVisibleManifest(visibleLinks(), source);
      } else if (source.type === 'bangumi') {
        manifest = makeManifest(await requestJSON(`https://api.bilibili.com/pgc/view/web/season?${source.query}`));
      } else if (source.type === 'space_list' && modeSelect.value !== 'pages') {
        manifest = await fetchSpaceList(source.id);
      } else {
        const query = source.query || (currentPageBvid() && `bvid=${currentPageBvid()}`);
        if (!query) throw new Error('无法确定列表中的当前视频');
        const view = await requestJSON(`https://api.bilibili.com/x/web-interface/view?${query}`);
        manifest = modeSelect.value === 'pages' || !view.ugc_season ? makePagesManifest(view) : makeUgcManifest(view);
      }
      downloadManifest(manifest);
      status.textContent = `已导出 ${manifest.episodes.length} 条${manifest.complete ? '' : '（非全量，请核对）'}`;
    } catch (error) {
      status.textContent = `导出失败：${error.message}`;
    } finally {
      button.disabled = false;
    }
  });
})();
