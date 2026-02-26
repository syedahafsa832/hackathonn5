from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

class SizeEngineV3:
    """
    Deterministic size recommendation engine.
    Calculates size based on user measurements and product garment specs.
    LLM must NOT perform these calculations.
    """

    def recommend_size(self, user_profile: Dict[str, Any], product_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Predict the best size for a user.
        
        Args:
            user_profile: {
                "height": int (cm),
                "weight": int (kg),
                "build_type": str ("slim", "average", "athletic", "heavier"),
                "fit_preference": str ("tight", "true", "loose")
            }
            product_data: {
                "fit_type": str ("slim", "relaxed", "cropped", "oversized"),
                "stretch_level": int (0 to 3),
                "size_chart": dict (Optional mapping of sizes to measurements)
        Inputs: 
          - user_profile: {height (cm), weight (kg), build_type, fit_preference}
          - product_data: {fit_type, stretch_level, model_height, variants}
        """
        try:
            height = user_profile.get("height")
            weight = user_profile.get("weight")
            fit_type = product_data.get("fit_type", "tailored")
            stretch = product_data.get("stretch_level", 0)
            
            if not height or not weight:
                return {"error": "Missing biometric data (height/weight) for sizing recommendation."}

            # 1. Base BMI-based sizing (Standard starting point)
            bmi = weight / ((height / 100) ** 2)
            
            # Simple BMI brackets for Aurelio & Finch standard blocks
            if bmi < 18.5: base_size = "XS"
            elif 18.5 <= bmi < 23: base_size = "S"
            elif 23 <= bmi < 27: base_size = "M"
            elif 27 <= bmi < 31: base_size = "L"
            elif 31 <= bmi < 35: base_size = "XL"
            else: base_size = "XXL"

            # 2. Adjust for Fit Type
            size_chart = ["XS", "S", "M", "L", "XL", "XXL"]
            current_index = size_chart.index(base_size)
            
            # If slim fit and they want comfort, size up
            if fit_type == "slim" and user_profile.get("fit_preference") == "relaxed":
                current_index = min(len(size_chart)-1, current_index + 1)
            
            # If relaxed fit and they want sharp look, size down
            if fit_type == "relaxed" and user_profile.get("fit_preference") == "slim":
                current_index = max(0, current_index - 1)

            # 3. Height Bias Adjustment
            # Over 188cm (6'2"), we add a height warning
            height_warning = None
            if height > 188:
                height_warning = "Note: You are taller than our standard model. Recommending 'Tall' variant if available."

            # 4. Stretch Adjustment
            # Stretch 3 (High) allows for tighter fits without discomfort
            reproducibility = 0.85 # Confidence score
            if stretch >= 3:
                reproducibility = 0.95

            recommended_size = size_chart[current_index]

            return {
                "success": True,
                "recommended_size": recommended_size,
                "confidence": reproducibility,
                "reasoning": f"Based on your BMI ({bmi:.1f}) and the {fit_type} fit profile. {height_warning or ''}"
            }

        except Exception as e:
            logger.error(f"Sizing Engine Error: {e}")
            return {"error": "Calculation error in sizing engine."}

size_engine = SizeEngineV3()
