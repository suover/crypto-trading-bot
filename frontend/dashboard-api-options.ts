type ApiFlags = { research?: unknown; overview?: unknown; analysis?: unknown };

export function resolveDashboardApis(deployed: boolean, development: boolean, flags: ApiFlags) {
  return {
    research: deployed || (development && flags.research === 'true'),
    overview: deployed || (development && flags.overview === 'true'),
    analysis: deployed || (development && flags.analysis === 'true'),
  };
}
