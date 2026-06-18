const express = require('express');
const cors = require('cors');
const path = require('path');
const projectService = require('./services/project_service');

const app = express();
const PORT = process.env.PORT || 3001;

app.use(cors());
app.use(express.json());

app.post('/api/projects', async (req, res) => {
    try {
        const projectData = req.body;
        const projectState = await projectService.createProject(projectData);
        res.status(201).json({ message: 'Project created successfully', projectState });
    } catch (error) {
        console.error('Error creating project:', error);
        res.status(500).json({ error: error.message || 'Failed to create project' });
    }
});

app.listen(PORT, () => {
    console.log(`Server is running on port ${PORT}`);
});
