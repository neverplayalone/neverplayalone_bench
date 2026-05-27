const fs = require('fs')
const path = require('path')

const root = __dirname
const headlessPath = path.join(root, 'node_modules', 'prismarine-viewer', 'lib', 'headless.js')

if (fs.existsSync(headlessPath)) {
  let src = fs.readFileSync(headlessPath, 'utf8')
  src = src.replace(
    "module.exports = (bot, { viewDistance = 6, output = 'output.mp4', frames = -1, width = 512, height = 512, logFFMPEG = false, jpegOptions }) => {",
    "module.exports = (bot, { viewDistance = 6, output = 'output.mp4', frames = -1, width = 512, height = 512, fps = 20, logFFMPEG = false, jpegOptions }) => {"
  )
  src = src.replace(
    "client = spawn('ffmpeg', ['-y', '-i', 'pipe:0', output])",
    "client = spawn('ffmpeg', ['-y', '-f', 'image2pipe', '-vcodec', 'mjpeg', '-framerate', String(fps), '-i', 'pipe:0', '-r', String(fps), '-pix_fmt', 'yuv420p', output])"
  )
  src = src.replace('setTimeout(update, 16)', 'setTimeout(update, 1000 / fps)')
  fs.writeFileSync(headlessPath, src)
}

const entitiesPath = path.join(root, 'node_modules', 'prismarine-viewer', 'viewer', 'lib', 'entities.js')
if (fs.existsSync(entitiesPath)) {
  let src = fs.readFileSync(entitiesPath, 'utf8')
  src = src.replace(
    'if (entity.username !== undefined) {',
    "if (entity.username !== undefined && process.env.MCBENCH_REC_SHOW_NAMES === '1') {"
  )
  fs.writeFileSync(entitiesPath, src)
}

const textureRoot = path.join(root, 'node_modules', 'prismarine-viewer', 'public', 'textures')
for (const version of ['1.16.4']) {
  const entityDir = path.join(textureRoot, version, 'entity')
  const alex = path.join(entityDir, 'alex.png')
  const steve = path.join(entityDir, 'steve.png')
  if (fs.existsSync(alex) && !fs.existsSync(steve)) {
    try {
      fs.symlinkSync('alex.png', steve)
    } catch {
      fs.copyFileSync(alex, steve)
    }
  }
}
