/* Language preference for the web app: English or Hindi.

   This app already shows both languages together everywhere (Hindi as the big primary line, English
   as a small subtitle) -- that dual-language layout stays as-is; it already serves a reader of either
   language and rewriting every screen to hide one language would be a much larger, riskier change
   than what "add a language switcher" needs. What this module controls is the *chrome* that has a
   single, structured bilingual source already (web/authz.js's NAV/GROUPS `{hi, en}` pairs, and the
   labels below): which of the two lines is primary, and a few standalone chrome strings that only
   exist as one string today (Save/Cancel/Back/Logout/...).

   Default is English, always -- this deliberately never reads navigator.language (a browser set to
   Hindi must not silently change the app's language). Once a person switches, the choice is
   remembered per-device via localStorage (this app is a static SPA with no server session to store
   it in; localStorage is the closest equivalent to "sticks across visits on this device"). */
(function () {
  'use strict';

  const KEY = 'lang';
  const LANGS = [
    { code: 'en', label: 'English' },
    { code: 'hi', label: 'हिन्दी' },
  ];
  const DEFAULT = 'en';

  const STRINGS = {
    en: {
      changePassword: 'Change Password', logout: 'Logout', language: 'Language',
      testAs: 'Test as', realLogin: 'Real login', menu: 'Menu', account: 'Account',
    },
    hi: {
      changePassword: 'पासवर्ड बदलें', logout: 'लॉग आउट', language: 'भाषा',
      testAs: 'इस रूप में जाँचें', realLogin: 'असली लॉगिन', menu: 'मेन्यू', account: 'खाता',
    },
  };

  function getLang() {
    let saved;
    try { saved = localStorage.getItem(KEY); } catch (e) { /* private mode: ignore */ }
    return LANGS.some(l => l.code === saved) ? saved : DEFAULT;
  }

  function setLang(code) {
    if (!LANGS.some(l => l.code === code)) return;
    try { localStorage.setItem(KEY, code); } catch (e) { /* private mode: ignore, just won't persist */ }
    document.documentElement.lang = code;
  }

  // t('changePassword') -> the string in the current language; missing key -> the key itself.
  function t(key) {
    const lang = getLang();
    return (STRINGS[lang] && STRINGS[lang][key]) || (STRINGS.en[key]) || key;
  }

  // Pick whichever of a {hi, en} pair is "primary" for the current language, and the other as the
  // small secondary line -- same pair, just swapped, matching the app's existing primary+sub layout.
  function primary(pair) { return getLang() === 'hi' ? pair.hi : pair.en; }
  function secondary(pair) { return getLang() === 'hi' ? pair.en : pair.hi; }

  document.documentElement.lang = getLang();

  window.I18n = { LANGS, getLang, setLang, t, primary, secondary };
})();
