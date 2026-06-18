import { useState } from 'react';

function ProjectForm() {
  const [formData, setFormData] = useState({
    projectName: '',
    script: '',
    aspectRatio: '16:9',
    sceneCount: 0,
    imagePlatform: 'chatgpt',
    videoPlatform: 'grok'
  });
  const [status, setStatus] = useState({ type: '', message: '' });
  const [isLoading, setIsLoading] = useState(false);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: name === 'sceneCount' ? parseInt(value) || 0 : value
    }));
  };

  const calculateScenes = () => {
    // Basic heuristic: 1 scene per 100 chars, max 40
    const chars = formData.script.length;
    let estimated = Math.ceil(chars / 100);
    if (estimated > 40) estimated = 40;
    if (estimated < 1 && chars > 0) estimated = 1;

    setFormData(prev => ({ ...prev, sceneCount: estimated }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setIsLoading(true);
    setStatus({ type: '', message: '' });

    try {
      const response = await fetch('http://localhost:3001/api/projects', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(formData),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || 'Failed to create project');
      }

      setStatus({ type: 'success', message: `Project "${formData.projectName}" created successfully!` });
      // Reset form on success (optional, or redirect)
      setFormData({
        projectName: '',
        script: '',
        aspectRatio: '16:9',
        sceneCount: 0,
        imagePlatform: 'chatgpt',
        videoPlatform: 'grok'
      });
    } catch (error) {
      setStatus({ type: 'error', message: error.message });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit}>
      {status.message && (
        <div className={`alert alert-${status.type}`}>
          {status.message}
        </div>
      )}

      <div className="form-group">
        <label htmlFor="projectName">Project Name (Unique identifier)</label>
        <input
          type="text"
          id="projectName"
          name="projectName"
          className="form-control"
          value={formData.projectName}
          onChange={handleChange}
          required
          pattern="[a-zA-Z0-9_-]+"
          title="Only letters, numbers, hyphens, and underscores are allowed"
        />
      </div>

      <div className="form-group">
        <label htmlFor="script">Full Video Script</label>
        <textarea
          id="script"
          name="script"
          className="form-control"
          value={formData.script}
          onChange={handleChange}
          required
          placeholder="Enter the complete script here..."
        />
      </div>

      <div className="form-group">
        <label htmlFor="sceneCount">
          Scene/Image Count (Max 40)
          {formData.script.length > 0 && (
            <button
              type="button"
              onClick={calculateScenes}
              style={{ marginLeft: '10px', fontSize: '0.8rem', padding: '2px 8px' }}
              className="btn btn-primary"
            >
              Suggest based on script
            </button>
          )}
        </label>
        <input
          type="number"
          id="sceneCount"
          name="sceneCount"
          className="form-control"
          value={formData.sceneCount}
          onChange={handleChange}
          min="1"
          max="40"
          required
        />
      </div>

      <div className="form-group" style={{ display: 'flex', gap: '2rem' }}>
        <div style={{ flex: 1 }}>
          <label htmlFor="aspectRatio">Video Size/Aspect Ratio</label>
          <select
            id="aspectRatio"
            name="aspectRatio"
            className="form-control"
            value={formData.aspectRatio}
            onChange={handleChange}
          >
            <option value="16:9">16:9 (Horizontal, 1920x1080)</option>
            <option value="9:16">9:16 (Vertical, 1080x1920)</option>
          </select>
        </div>
      </div>

      <div className="form-group" style={{ display: 'flex', gap: '2rem' }}>
        <div style={{ flex: 1 }}>
          <label htmlFor="imagePlatform">Image Generation Platform</label>
          <select
            id="imagePlatform"
            name="imagePlatform"
            className="form-control"
            value={formData.imagePlatform}
            onChange={handleChange}
          >
            <option value="chatgpt">ChatGPT (DALL-E 3)</option>
            <option value="gemini">Google Gemini (Imagen 3)</option>
            <option value="grok">Grok (Flux)</option>
          </select>
        </div>

        <div style={{ flex: 1 }}>
          <label htmlFor="videoPlatform">Video Generation Platform</label>
          <select
            id="videoPlatform"
            name="videoPlatform"
            className="form-control"
            value={formData.videoPlatform}
            onChange={handleChange}
          >
            <option value="grok">Grok</option>
            <option value="meta">Meta AI</option>
          </select>
        </div>
      </div>

      <button type="submit" className="btn btn-primary" disabled={isLoading}>
        {isLoading ? 'Creating Project...' : 'Create Project'}
      </button>
    </form>
  );
}

export default ProjectForm;
