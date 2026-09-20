/* Where the data API (the Django server) lives.
   This is the ONLY line to change after the API is hosted:

     apiBase: 'https://<render-service-name>.onrender.com/api/'

   - Must be the full address and end with a slash.
   - Leave it empty ('') only when the API and this page come from the same server
     (local development): the app then uses "../api/" automatically. */
window.APP_CONFIG = {
  apiBase: 'https://expense-tracking-api-0wso.onrender.com/api/',
  // Phase 1 testing only: shows a "Test as" switcher (owner/manager/viewer/... @test.com) in the header.
  // It only changes what the UI shows, not what the server allows. Set to false for real use.
  demoRoles: false,
};
