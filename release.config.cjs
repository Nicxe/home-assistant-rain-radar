const path = require("path");

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

const execPlugin = config.plugins.find(
  (plugin) => Array.isArray(plugin) && plugin[0] === "@semantic-release/exec"
);

if (githubPlugin?.[1]) {
  githubPlugin[1].successCommentCondition = false;
}

if (execPlugin?.[1]) {
  const sharedConfigDir = path.dirname(
    require.resolve("@nicxe/semantic-release-config/package.json")
  );
  const updateManifestScript = path.join(
    sharedConfigDir,
    ".release",
    "update-manifest-version.js"
  );

  execPlugin[1].prepareCmd = [
    `node ${JSON.stringify(updateManifestScript)} --file "custom_components/rain_radar/manifest.json" --version "\${nextRelease.version}"`,
    "&& ./scripts/build-release-zip.sh"
  ].join(" ");
}

module.exports = config;
