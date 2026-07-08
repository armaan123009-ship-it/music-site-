import React from 'react';
import { motion } from 'framer-motion';
import { Monitor, Palette, Zap } from 'lucide-react';

// Reusable Feature Card Component
interface FeatureCardProps {
  title: string;
  description: string;
  icon: React.ReactNode;
  gradient: string;
  delay: number;
}

const FeatureCard: React.FC<FeatureCardProps> = ({ title, description, icon, gradient, delay }) => {
  return (
    <motion.div
      initial={{ opacity: 0, y: 30 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.8, ease: 'easeOut', delay }}
      className="relative flex flex-col justify-start items-start w-full max-w-[260px] md:max-w-[300px] group mx-auto"
    >
      {/* Glow Background (Crucial) */}
      <div
        className="absolute inset-0 w-full h-[260px] md:h-[300px] opacity-60 rounded-[40px] pointer-events-none transition-transform duration-500 group-hover:scale-105"
        style={{
          background: gradient,
          filter: 'blur(45px)',
        }}
      />

      {/* Foreground Card with Gradient Border (Crucial) */}
      <div
        className="self-stretch h-[260px] md:h-[300px] rounded-[40px] z-10 overflow-hidden border-8 border-transparent transition-transform duration-500 group-hover:scale-[1.02]"
        style={{
          background: `linear-gradient(#1A1A1C, #1A1A1C) padding-box, ${gradient} border-box`,
        }}
      >
        {/* Content Inner Layout */}
        <div className="w-full h-full p-7 flex flex-col justify-between">
          <div className="text-white/90">
            {icon}
          </div>
          <div>
            <h3 className="text-white font-medium text-xl mb-3 tracking-tight">{title}</h3>
            <p className="text-gray-400 text-[14px] leading-[1.6] font-normal selection:bg-white/20">
              {description}
            </p>
          </div>
        </div>
      </div>
    </motion.div>
  );
};

export default function App() {
  return (
    <div className="min-h-screen bg-[#0A0A0B] flex flex-col items-center justify-center p-6 md:p-12 font-sans overflow-hidden">
      {/* Background Decorative Accent */}
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[500px] h-[500px] bg-indigo-500/5 rounded-full blur-[120px] pointer-events-none" />

      {/* Main Container */}
      <div className="relative z-10 w-full max-w-[936px] flex flex-col items-center space-y-12">
        {/* Optional Header for Visual Premium Feel */}
        <div className="text-center space-y-3">
          <span className="text-[11px] font-bold text-indigo-400 uppercase tracking-[0.25em]">Premium Features</span>
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-black text-white tracking-tighter">
            Stunning Glow Aesthetics
          </h1>
          <p className="text-gray-500 text-sm md:text-base max-w-md mx-auto">
            Perfect replication of glassmorphism glow cards built with React 19, Vite, and Tailwind CSS v4.
          </p>
        </div>

        {/* Feature Cards CSS Grid */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-10 md:gap-3 lg:gap-3 w-full justify-center">
          {/* Card 1: Hardware */}
          <FeatureCard
            title="Hardware"
            delay={0.1}
            description="My entire desktop setup is built for power. It is silent, durable, and holds my focus."
            gradient="linear-gradient(137deg, #FF3D77 0%, #FFB1CE 45%, #FF9D3C 100%)"
            icon={<Monitor className="w-8 h-8 stroke-[2.5]" />}
          />

          {/* Card 2: Studio */}
          <FeatureCard
            title="Studio"
            delay={0.2}
            description="Studio is where I define every single pixel. It is the hub for each canvas I deliver."
            gradient="linear-gradient(137deg, #FFFFFF 0%, #7DD3FC 45%, #06B6D4 100%)"
            icon={<Palette className="w-8 h-8 stroke-[2.5]" />}
          />

          {/* Card 3: Motion */}
          <FeatureCard
            title="Motion"
            delay={0.3}
            description="I use Motion to build lively prototypes, bridging the gap between views and code."
            gradient="linear-gradient(137deg, #4361EE 0%, #E0AEFF 45%, #F72585 100%)"
            icon={<Zap className="w-8 h-8 stroke-[2.5]" />}
          />
        </div>
      </div>
    </div>
  );
}
