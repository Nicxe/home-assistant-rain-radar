const config = require("@nicxe/semantic-release-config")({
  componentDir: "custom_components/rain_radar",
  manifestPath: "custom_components/rain_radar/manifest.json",
  projectName: "Rain Radar",
  repoSlug: "Nicxe/home-assistant-rain-radar",
  assets: [
    {
      path: "custom_components/rain_radar.zip",
      label: "rain_radar.zip"
    }
  ]
});

const githubPlugin = config.plugins.find(
  (plugin) => Array.isArray(plugin) && plugin[0] === "@semantic-release/github"
);

if (githubPlugin?.[1]) {
  githubPlugin[1].successCommentCondition = false;
}

module.exports = config;

