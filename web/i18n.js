/* Language preference for the web app: English or Hindi.

   Every screen shows ONE language at a time, picked by this switcher -- never both languages mixed
   together. web/app.js's `L(hi, en)` helper reads the current language (via getLang() below) to pick
   which string to render; this module itself covers the app *chrome* (user menu, nav, Settings) via
   t()/primary(), using the STRINGS table and web/authz.js's NAV/GROUPS `{hi, en}` pairs.

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
      settings: 'Settings',
      projectsTab: 'Projects', usersAccessTab: 'Users & access',
      newProject: 'New project', projectCode: 'Code', projectName: 'Name', location: 'Location',
      status: 'Status', plotSize: 'Plot size', startDate: 'Start date',
      expectedCompletion: 'Expected completion', remarks: 'Remarks', membersCount: 'Members',
      statusPlanned: 'Planned', statusOngoing: 'Ongoing', statusCompleted: 'Completed', statusArchived: 'Archived',
      projectMembers: 'Project members', addMember: 'Add member', selectUser: 'Select user',
      remove: 'Remove', role: 'Role', save: 'Save', cancel: 'Cancel', createProject: 'Create project',
      codeAutoHint: 'Leave blank to auto-generate a code.', noProjectsYet: 'No projects yet.', noMembersYet: 'No members yet.',
      saved: 'Saved.', memberAdded: 'Member added.', memberRemoved: 'Removed.', roleChanged: 'Role changed.',
      confirmRemoveMember: 'Remove this person from the project?',
      nameRequired: 'Enter a project name.',
    },
    hi: {
      changePassword: 'पासवर्ड बदलें', logout: 'लॉग आउट', language: 'भाषा',
      testAs: 'इस रूप में जाँचें', realLogin: 'असली लॉगिन', menu: 'मेन्यू', account: 'खाता',
      settings: 'सेटिंग',
      projectsTab: 'प्रोजेक्ट', usersAccessTab: 'यूज़र और एक्सेस',
      newProject: 'नया प्रोजेक्ट', projectCode: 'कोड', projectName: 'नाम', location: 'जगह',
      status: 'स्थिति', plotSize: 'प्लॉट साइज़', startDate: 'शुरू की तारीख',
      expectedCompletion: 'पूरा होने की तारीख', remarks: 'नोट', membersCount: 'सदस्य',
      statusPlanned: 'शुरू होना बाकी', statusOngoing: 'काम चालू है', statusCompleted: 'काम पूरा हो गया', statusArchived: 'आर्काइव',
      projectMembers: 'प्रोजेक्ट सदस्य', addMember: 'सदस्य जोड़ें', selectUser: 'यूज़र चुनें',
      remove: 'हटाएँ', role: 'भूमिका', save: 'सेव करें', cancel: 'रद्द करें', createProject: 'प्रोजेक्ट बनाएँ',
      codeAutoHint: 'खाली छोड़ें तो कोड अपने आप बन जाएगा.', noProjectsYet: 'अभी कोई प्रोजेक्ट नहीं है.',
      saved: 'सेव हो गया.', memberAdded: 'सदस्य जुड़ गया.', memberRemoved: 'हटा दिया.', roleChanged: 'भूमिका बदल गई.', noMembersYet: 'अभी कोई सदस्य नहीं है.',
      confirmRemoveMember: 'इस व्यक्ति को प्रोजेक्ट से हटाएँ?',
      nameRequired: 'प्रोजेक्ट का नाम भरें.',
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

  // Pick whichever of a {hi, en} pair matches the current language -- e.g. web/authz.js's NAV entries.
  function primary(pair) { return getLang() === 'hi' ? pair.hi : pair.en; }

  document.documentElement.lang = getLang();

  window.I18n = { LANGS, getLang, setLang, t, primary };
})();
