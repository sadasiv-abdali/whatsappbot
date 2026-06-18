const fs = require('fs').promises;
const path = require('path');

const PROJECTS_DIR = path.join(__dirname, '..', '..', '..', 'projects');

async function createProject(config) {
    const { projectName, script, aspectRatio, sceneCount, imagePlatform, videoPlatform } = config;

    if (!projectName) {
        throw new Error('Project name is required');
    }

    const projectPath = path.join(PROJECTS_DIR, projectName);

    try {
        await fs.access(projectPath);
        throw new Error('Project already exists');
    } catch (err) {
        if (err.code !== 'ENOENT') throw err;
        // Project doesn't exist, proceed
    }

    // Create folders
    await fs.mkdir(projectPath, { recursive: true });
    await fs.mkdir(path.join(projectPath, 'images'), { recursive: true });
    await fs.mkdir(path.join(projectPath, 'videos'), { recursive: true });
    await fs.mkdir(path.join(projectPath, 'final'), { recursive: true });

    const now = new Date().toISOString();

    const initialState = {
        project_name: projectName,
        created_at: now,
        updated_at: now,
        config: {
            script: script || "",
            aspect_ratio: aspectRatio || "16:9",
            scene_count: sceneCount || 0,
            image_platform: imagePlatform || "chatgpt",
            video_platform: videoPlatform || "grok"
        },
        status: "pending",
        scenes: [],
        final_video_path: null,
        queue_logs: []
    };

    const stateFilePath = path.join(projectPath, 'project_state.json');
    await fs.writeFile(stateFilePath, JSON.stringify(initialState, null, 2), 'utf-8');

    return initialState;
}

module.exports = {
    createProject
};
