import uvicorn
import sys

if __name__ == "__main__":
    sys.path.append("C:/Users/ela35/OneDrive/Documents/cour/IOT/projet/backend")
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)