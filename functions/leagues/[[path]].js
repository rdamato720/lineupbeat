import {sharedLeagueRedirect} from '../../_shared/league-history-api.mjs';

export function onRequestGet(context) {
  return sharedLeagueRedirect(context.request);
}
